"""Pre-flight (T4.9): refuse a live write run if the platform drifted from the fixture.

On Keystone the write allow-list is pinned to the 9 original Incoming files. Those 9
must be in Incoming, unchanged since the fixture and still tagged 'untriaged'.
Any other row in Incoming can never be written (the guard refuses it and triage
leaves it for a person), so it is a WARNING, not a reason to refuse, provided the
fixture already knew it and it has not changed since. An unknown or changed extra
stays a PROBLEM: duplicate grouping reads every file, so a new row could change how
the 9 are judged and the live expectations would no longer hold. For the same reason,
a row ANYWHERE that is new, changed or gone since the fixture is a PROBLEM if it is
related to one of the 9: it shares a name (duplicate grouping compares names) or a
recorded hash, or it is the same kind of document (the similar-file signal counts
every file of that doc type, or every drawing with that code prefix). A fixture row
that has left Incoming is a PROBLEM. Triage files by folder NAME, and folder names feed
the description and default-folder signals, so any folder added, removed, renamed,
moved or archived since the fixture (or two live folders with the same name) is a
PROBLEM too, and on the live platform the fixture itself must be a fresh capture.

Every name in a message is printable text on one line, and rows of apps this seat
can't open are named by their placeholder. Where the platform says who made a change,
the message says so, so the team knows whom to ask.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.catalog import Catalog
from agent.config import KEYSTONE_INCOMING_ALLOWLIST, WRITE_BUSINESS
from agent.privacy import sanitise_file
from agent.safe_reads import folder_named, folders_by_id, list_all
from agent.skills.duplicates import stem
from agent.skills.profiles import doc_type_of
from agent.skills.revisions import code_prefix, part_code
from agent.textsafe import one_line, printable

LABEL_CHARS = 120
FIXTURE_MAX_AGE = timedelta(hours=24)
FOLDER_PLACE_FIELDS = ("name", "parent_id", "is_archived")  # PR #4: what triage and the folder signals read
FOLDER_FIELDS = FOLDER_PLACE_FIELDS + ("updated_at",)  # STRIDE T4: any other update to a folder counts too
RECOVERY = ("find out who changed what (above), then re-capture and rehearse before trying again: "
            "python -m harness capture keystone; python -m harness run TI2L --target fake; python -m harness preflight")


class PreflightFailed(Exception):
    pass


def _label(row: dict[str, Any], can_list: Any) -> str:
    """STRIDE I7/S2: a row's name for a message: its placeholder if withheld, always one printable line."""
    return one_line(sanitise_file(row, can_list).get("filename"), LABEL_CHARS)


def _by(row: dict[str, Any] | None, created: bool = False, when: bool = True) -> str:
    """STRIDE D5: ' (by <who> at <when>)' for the change, or '' when the platform does not record who."""
    who = (row or {}).get("created_by" if created else "updated_by")
    at = f" at {one_line((row or {}).get('created_at' if created else 'updated_at'), 40)}" if when else ""
    return f" (by {one_line(who, 60)}{at})" if who else ""


def _drift(file_id: str, row: dict[str, Any], base: dict[str, Any] | None, need_untriaged: bool, can_list: Any) -> str | None:
    """The first way this Incoming row differs from the fixture, or None."""
    label = _label(row, can_list)
    if base is None:
        return f"{label} ({file_id}) is not in the fixture{_by(row, created=True)}"
    if row.get("updated_at") != base.get("updated_at"):
        return f"{label} changed since the fixture (updated_at {one_line(row.get('updated_at'), 40)}){_by(row, when=False)}"
    if row.get("folder_id") != base.get("folder_id"):
        return f"{label} moved into Incoming since the fixture{_by(row)}"
    if need_untriaged and "untriaged" not in (row.get("tags") or ""):
        return f"{label} is no longer tagged 'untriaged'{_by(row)}"
    return None


def assess(rt: Any, fixture: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(problems, warnings). Any problem refuses the run; warnings are reported and allowed."""
    problems = list(rt.catalog.check_required()) + _fixture_problems(rt, fixture)
    # No escalation tool only disables escalating; the tasks that need one say so (needs_tools) and are skipped.
    warnings = [] if rt.catalog.escalation_tool() else [
        f"no escalation tool ({', '.join(rt.catalog.missing_optional())} missing): files that need a person are "
        "left in place and named in the answer, not escalated"]
    fixture_hash = (fixture.get("_manifest") or {}).get("tool_hash")
    if not fixture_hash:
        problems.append("the fixture manifest has no tool hash: re-capture fixtures")
    elif fixture_hash != rt.catalog.hash():
        problems.append(f"tool catalogue changed (fixture {fixture_hash}, live {rt.catalog.hash()}): re-capture fixtures")
    if Catalog.from_tools(fixture.get("tools") or []).text_hash() != rt.catalog.text_hash():  # STRIDE T10
        problems.append("the description or read-only mark of a tool the agent uses changed since the fixture: "
                        "re-capture fixtures")
    folders = folders_by_id(rt.admin_mcp)
    problems += _folder_problems(folders, fixture)  # PR #4 and STRIDE T4: one folder check
    incoming = folder_named(folders, "Incoming")
    if not incoming:
        return problems + ["no Incoming folder"], warnings
    can_list = rt.catalog.can_list
    live_rows = {r["id"]: r for r in list_all(rt.admin_mcp, "FileAttachment.list", folder_id=incoming["id"])}
    required = KEYSTONE_INCOMING_ALLOWLIST if rt.settings.business == WRITE_BUSINESS else frozenset(live_rows)
    missing = sorted(required - set(live_rows))
    if missing:
        problems.append(f"allow-listed file(s) missing from Incoming: {missing}")
    baseline = {r["id"]: r for r in fixture["tables"]["FileAttachment"]}
    for file_id, row in sorted(live_rows.items()):
        in_scope = file_id in required
        drift = _drift(file_id, row, baseline.get(file_id), in_scope, can_list)
        if drift:
            problems.append(drift if in_scope else f"{drift} (outside the allow-list, but the live expectations assume the fixture's Incoming)")
        elif not in_scope:
            warnings.append(f"{_label(row, can_list)} ({file_id}) is in Incoming but outside the write allow-list: "
                            "it will not be written or escalated")
    problems += _outside_incoming(rt, baseline, live_rows, required, incoming["id"])
    return problems, warnings


def _fixture_problems(rt: Any, fixture: dict[str, Any]) -> list[str]:
    """STRIDE T8: on the live platform the baseline must be a fresh capture, as docs/live-run.md (section 11) requires."""
    if rt.target != "live":
        return []
    captured = (fixture.get("_manifest") or {}).get("captured_at")
    try:
        when = datetime.fromisoformat(str(captured))
    except ValueError:
        return ["the fixture manifest has no valid captured_at: re-capture fixtures"]
    age = datetime.now(timezone.utc) - (when if when.tzinfo else when.replace(tzinfo=timezone.utc))
    if age.total_seconds() < 0 or age > FIXTURE_MAX_AGE:
        return [f"the fixture was captured at {one_line(captured, 40)}, not within the last "
                f"{int(FIXTURE_MAX_AGE.total_seconds() // 3600)} hours: re-capture fixtures right before the live run"]
    return []


def _folder_problems(live: dict[str, dict[str, Any]], fixture: dict[str, Any]) -> list[str]:
    """STRIDE T4 / PR #4: triage resolves destinations by folder name and folder names feed the description and
    default-folder signals, so the folders must be exactly the fixture's (added, gone, renamed, moved, archived or
    otherwise updated), and no two live folders may share a name."""
    base = {f["id"]: f for f in fixture["tables"].get("DriveFolder", [])}
    # PR #4: a fixture without folders is one problem, not one "not in the fixture" line per live folder.
    problems = [] if base else ["the fixture has no DriveFolder table: re-capture fixtures"]
    for folder_id in sorted(set(base) | set(live)) if base else ():
        now, then = live.get(folder_id), base.get(folder_id)
        if then is None:
            problems.append(f"folder '{one_line(now.get('name'), LABEL_CHARS)}' ({folder_id}) is not in the fixture"
                            f"{_by(now, created=True)}")
        elif now is None:
            problems.append(f"folder '{one_line(then.get('name'), LABEL_CHARS)}' ({folder_id}) is gone since the fixture")
        elif any(now.get(k) != then.get(k) for k in FOLDER_PLACE_FIELDS):
            problems.append(f"folder '{one_line(then.get('name'), LABEL_CHARS)}' ({folder_id}) was renamed, moved or "
                            f"archived since the fixture (now '{one_line(now.get('name'), LABEL_CHARS)}'){_by(now)}")
        elif any(now.get(k) != then.get(k) for k in FOLDER_FIELDS):
            problems.append(f"folder '{one_line(then.get('name'), LABEL_CHARS)}' ({folder_id}) changed since the fixture "
                            f"(updated_at {one_line(now.get('updated_at'), 40)}){_by(now, when=False)}")
    # Archived folders are never filing destinations (safe_reads.folder_named skips them), so they can't clash.
    names = Counter(" ".join(str(f.get("name") or "").split()).casefold() for f in live.values() if not f.get("is_archived"))
    problems += [f"{n} folders are named '{one_line(name, LABEL_CHARS)}'" for name, n in sorted(names.items()) if n > 1]
    return problems


def _outside_incoming(rt: Any, baseline: dict[str, dict[str, Any]], live_rows: dict[str, dict[str, Any]],
                      required: frozenset[str], incoming_id: str) -> list[str]:
    """Changes elsewhere that can still change how the allow-listed files are judged."""
    rules = rt.ctx.rules
    watched = [baseline[i] for i in required if i in baseline]
    names = {stem(r.get("filename") or "") for r in watched}
    hashes = {r["content_hash"] for r in watched if r.get("content_hash")}
    kinds = [(r.get("filename") or "", doc_type_of(r.get("filename") or "", rules)) for r in watched]
    types = {t.name for _, t in kinds if t and t.name != "drawing"}
    prefixes = {code_prefix(c) for f, t in kinds if t and t.name == "drawing" and (c := part_code(f))}
    can_list = rt.catalog.can_list

    def related(row: dict[str, Any]) -> bool:
        # STRIDE I7: judge a row as triage sees it. A withheld row only has its placeholder name, so its
        # real title can neither block the run nor be probed through pre-flight.
        seen = sanitise_file(row, can_list)
        filename = seen.get("filename") or ""
        if stem(filename) in names or seen.get("content_hash") in hashes:
            return True
        if (t := doc_type_of(filename, rules)) and t.name in types:
            return True
        return bool((c := part_code(filename)) and code_prefix(c) in prefixes)

    everywhere = {r["id"]: r for r in list_all(rt.admin_mcp, "FileAttachment.list")}
    problems = []
    for file_id, base in sorted(baseline.items()):
        if file_id in required or file_id in live_rows:
            continue
        if base.get("folder_id") == incoming_id:
            problems.append(f"{_label(base, can_list)} ({file_id}) has left Incoming since the fixture{_by(everywhere.get(file_id))}")
        elif related(base) and file_id not in everywhere:
            problems.append(f"{_label(base, can_list)} ({file_id}) is gone since the fixture and is related "
                            "to an allow-listed file (name, hash or document kind)")
    for file_id, row in sorted(everywhere.items()):
        if file_id in required or file_id in live_rows:
            continue
        base = baseline.get(file_id)
        if not (related(row) or (base is not None and related(base))):
            continue
        if base is None or any(row.get(k) != base.get(k) for k in ("updated_at", "folder_id")):
            problems.append(f"{_label(row, can_list)} ({file_id}) is new or changed since the fixture and is related "
                            f"to an allow-listed file (name, hash or document kind){_by(row, created=base is None)}")
    return problems


def check(rt: Any, fixture: dict[str, Any]) -> list[str]:
    """Problems found (empty list = safe to run)."""
    return assess(rt, fixture)[0]


def require_ok(rt: Any, fixture: dict[str, Any]) -> list[str]:
    """Raise PreflightFailed on any problem; otherwise return the warnings (also traced and printed)."""
    problems, warnings = assess(rt, fixture)
    rt.trace.write("preflight", ok=not problems, problems=problems, warnings=warnings,
                   fixture={"dir": fixture.get("_dir"), "hash": (fixture.get("_manifest") or {}).get("fixture_hash")})
    if warnings:
        print(printable("pre-flight warnings (not blocking):\n  - " + "\n  - ".join(warnings)), file=sys.stderr)
    if problems:
        raise PreflightFailed("Pre-flight failed; nothing was written:\n  - " + "\n  - ".join(problems))
    return warnings
