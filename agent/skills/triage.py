"""A2/A3/A4/A6/A7: evidence-scored triage of a folder, planned first, applied only in apply mode.

Scoring rules live in agent/filing_rules.toml (TEAM-OWNED). A description such as
"Belongs in HR" counts only when another, independent signal points to the same
folder; if it points somewhere else the file is flagged as a conflict, never moved.

Duplicate rule (decision C): the agent never writes to a file because it looks like a copy of
another. A file matching another on the recorded hash + size + name, or only on name + size, is a
possible copy: it is not filed, moved, archived or annotated, even if its own evidence would file
it, and (if allow-listed) it is escalated once, naming the other file's id, folder and in-run
destination. Hash, size and name are recorded metadata any seat can edit (bug B4), not the bytes.
The original (a plain name first, then the oldest) is scored and filed like any other file.
A later tidy does not escalate the same file again unless it was renamed (the subject is id + name).
Known limits: a copy with a different name stem or size is not detected; a copy whose recorded hash
is blank or differs from its original's is not detected (find_groups pairs on name + size only when
neither file has a trusted hash); an original can be filed into a folder that holds its copy under a
different name.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from agent.budget import BudgetExceeded
from agent.guards import StaleRow, WriteBlocked, WriteNotConfirmed
from agent.mcp_client import McpError
from agent.records import DecisionRecord, Evidence
from agent.safe_reads import find_files_by_name, folder_named, where
from agent.skills.common import NAME_LIMIT, UNVERIFIED_UPLOADER, Skill, SkillContext, error_text, ev, upload_lead
from agent.skills.duplicates import find_groups
from agent.skills.profiles import (PROVENANCE_MARKER, default_folder_name, description_destination,
                                   doc_type_of, similar_file_folder)
from agent.textsafe import one_line


@dataclass
class PlanItem:
    row: dict[str, Any]
    action: str  # move | conflict | escalate | refuse | leave
    to_folder: str | None = None
    score: int = 0
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    person: str | None = None
    person_source: str | None = None
    party_id: str | None = None
    original_id: str | None = None
    basis: str | None = None  # how a duplicate was matched


TRUSTED_DUPLICATE_BASIS = "recorded hash"
SAME_NAME_BASIS = "same filename in the destination"
OUT_OF_SCOPE = "not in the write allow-list"


@dataclass(frozen=True)
class Voters:
    """Which rows may vote on where a kind of file lives: not the folder being tidied, not ours."""
    skip: set[str]
    me: str | None
    own_ids: frozenset[str]


def _signals(ctx: SkillContext, row: dict[str, Any], files: list[dict[str, Any]], voters: Voters) -> list[Evidence]:
    rules, folders = ctx.rules, ctx.folders()
    out: list[Evidence] = []
    dtype = doc_type_of(row.get("filename", ""), rules)
    dest = None
    if dtype:
        similar = similar_file_folder(row, dtype, files, rules, voters.skip, voters.me, voters.own_ids)
        fallback = folder_named(folders, default_folder_name(row, dtype, rules) or "")
        dest = similar or (fallback or {}).get("id")
        out.append(ev("filename_pattern", f"filename looks like a {dtype.name}", dest))
        if similar:
            out.append(ev("similar_file_in_folder", f"other {dtype.name} files live in {ctx.folder_name(similar)}", similar))
    if row.get("entity_type") == "Item" and row.get("entity_id"):
        out.append(ev("linked_record", f"linked to Item {row['entity_id']}", dest if dtype and dtype.name == "drawing" else None, row["entity_id"]))
    desc_dest = description_destination(row.get("description"), folders)
    if desc_dest:
        out.append(ev("description", f"description names {ctx.folder_name(desc_dest)}", desc_dest))
    if row.get("party_id") or row.get("from_email"):
        out.append(ev("sender", f"sent by {_sender(row)}", None, row.get("party_id")))
    return out


def _sender(row: dict[str, Any]) -> str:
    # STRIDE T2: a party name is a display join any team can edit (Party.update); keep it one short plain line
    return one_line(row.get("_party_id_display") or row.get("from_name") or row.get("from_email"), NAME_LIMIT)


def _person(ctx: SkillContext, row: dict[str, Any]) -> tuple[str | None, str]:
    """Who to ask about a file, and where that came from. The access log is only a lead, and says so."""
    sender = _sender(row)
    if sender:
        return sender, "sender on the record"
    if row["id"] not in ctx.allowlist:  # STRIDE D1: no budgeted lookup for a file this run will not act on
        return None, "not looked up: out of scope"
    who, source = upload_lead(ctx, row["id"])
    return (f"{who} ({UNVERIFIED_UPLOADER})" if who else None), source


def _score(ctx: SkillContext, item: PlanItem) -> PlanItem:
    w = ctx.rules.weights
    placed = {e.points_to for e in item.evidence if e.points_to and e.signal not in ("description", "sender")}
    desc = next((e.points_to for e in item.evidence if e.signal == "description"), None)
    if len(placed) > 1 or (desc and placed and desc not in placed):
        names = ", ".join(sorted({ctx.folder_name(p) for p in placed | ({desc} if desc else set())}))
        item.action, item.missing = "conflict", [f"agreeing evidence (the signals point to {names})"]
        return item
    if not placed:
        has_any = bool(item.evidence)
        item.action = "escalate" if has_any else "refuse"
        item.missing = (["document type", "which folder it belongs in"] if has_any else ["file contents", "who sent it"])
        return item
    target = placed.pop()
    score = sum(w.get(e.signal, 0) for e in item.evidence if e.points_to == target)
    score += w.get("sender", 0) if any(e.signal == "sender" for e in item.evidence) else 0
    item.to_folder, item.score = target, score
    item.action = "move" if score >= ctx.rules.threshold else "escalate"
    if item.action == "escalate":
        item.missing = [f"stronger evidence (score {score} < threshold {ctx.rules.threshold})"]
    return item


def build_plan(ctx: SkillContext, folder: dict[str, Any], only_file: str | None) -> list[PlanItem]:
    files = ctx.files()
    in_folder = where(files, folder_id=folder["id"])
    if only_file:
        in_folder = find_files_by_name(in_folder, only_file)
    skip = {folder["id"], *(f["id"] for f in ctx.folders().values() if f.get("name", "").lower() == "superseded")}
    own_ids = frozenset(w["id"] for w in ctx.guard.writes if w.get("tool") == "FileAttachment.update" and w.get("id"))
    voters = Voters(skip, (ctx.me() or {}).get("id"), own_ids)
    groups = {c["id"]: g for g in find_groups(files) for c in g.copies}
    plan = [_plan_item(ctx, row, files, voters, groups.get(row["id"]))
            for row in sorted(in_folder, key=lambda r: r.get("filename", ""))]
    _hold_name_collisions(ctx, plan, files)
    for item in plan:  # after the same-name rule, so the request says where the other file really goes in this run
        if _is_possible_copy(item):
            item.missing = [_ask_to_compare(ctx, item, plan, files)]
    for item in plan:
        if item.action in ("escalate", "refuse", "conflict"):
            item.person, item.person_source = _person(ctx, item.row)
            item.party_id = item.row.get("party_id")
    return plan


def _plan_item(ctx: SkillContext, row: dict[str, Any], files: list[dict[str, Any]], voters: Voters, group: Any) -> PlanItem:
    if row.get("is_archived"):
        return PlanItem(row, "leave", missing=["nothing: it is already archived, so it was left as it is"])
    item = _score(ctx, PlanItem(row, "refuse", evidence=_signals(ctx, row, files, voters)))
    if group is None:
        return item
    return _hold_as_copy_match(ctx, item, group)


def _hold_as_copy_match(ctx: SkillContext, item: PlanItem, group: Any) -> PlanItem:
    """Decision C: a possible copy is never filed, moved, archived or annotated; a person is asked instead.

    The match (hash + size + name, or name + size) is recorded metadata any seat can edit, and no bytes
    are stored (bug B4). Acting on it would let a row planted anywhere in the tenant steer or hide this
    file, and would bypass the evidence score and the same-name rule (decision B). Both bases follow
    the same rule; the request text is filled in by _ask_to_compare once the whole plan is known.
    """
    original = group.original
    signal = "recorded_duplicate" if group.basis.startswith(TRUSTED_DUPLICATE_BASIS) else "suspected_duplicate"
    hint = ev(signal, f"matches {one_line(original.get('filename'), NAME_LIMIT)} in {ctx.folder_name(original.get('folder_id'))} "
              f"on {group.basis}: recorded metadata, not byte-verified", None, original["id"])
    return PlanItem(item.row, "escalate", evidence=[*item.evidence, hint], original_id=original["id"], basis=group.basis)


def _is_possible_copy(item: PlanItem) -> bool:
    """Held by decision C (a duplicate group match), as opposed to the same-name rule (decision B)."""
    return bool(item.original_id) and item.basis not in (None, SAME_NAME_BASIS)


def _ask_to_compare(ctx: SkillContext, item: PlanItem, plan: list[PlanItem], files: list[dict[str, Any]]) -> str:
    """Name the other file by id and folder, and where this run is filing it, and ask a person to decide."""
    other = next((f for f in files if f.get("id") == item.original_id), {"id": item.original_id})
    twin = next((p for p in plan if p.row["id"] == item.original_id), None)
    filed = bool(twin and twin.action == "move" and twin.to_folder and twin.row["id"] in ctx.allowlist)
    going = f", which this run plans to file in {ctx.folder_name(twin.to_folder)}" if filed else ""
    return (f"a person to compare it with its possible duplicate {one_line(other.get('filename'), NAME_LIMIT)} "
            f"({other['id']}, in {ctx.folder_name(other.get('folder_id'))}{going}) and decide whether to file it, "
            f"keep both, or have one removed; matched only on {item.basis}, recorded metadata any seat can edit, "
            "not the file bytes")


def _name_key(row: dict[str, Any]) -> str:
    return (row.get("filename") or "").strip().casefold()


def _hold_name_collisions(ctx: SkillContext, plan: list[PlanItem], files: list[dict[str, Any]]) -> None:
    """Never put two files with the same name into one folder without a person deciding.

    A planned move is held back when its destination already holds a file of that name, or when
    another file of that name is planned into the same destination. Of the planned ones, only a
    single highest scorer is filed; the others (and a tie) are escalated as possible copies.
    """
    groups: dict[tuple[str, str], list[PlanItem]] = defaultdict(list)
    for item in plan:  # only files this run may write compete: an out-of-scope file stays where it is
        if item.action == "move" and item.to_folder and item.row["id"] in ctx.allowlist:
            groups[(item.to_folder, _name_key(item.row))].append(item)
    for (dest, name), items in groups.items():
        ids = {i.row["id"] for i in items}
        present = [f for f in files if f.get("folder_id") == dest and _name_key(f) == name
                   and f["id"] not in ids and not f.get("is_trashed")]
        ranked = sorted(items, key=lambda i: i.score, reverse=True)
        tie = len(ranked) > 1 and ranked[0].score == ranked[1].score
        keep = None if present or tie else ranked[0]
        for item in ranked:
            if item is not keep:
                others = present or ([keep.row] if keep else [i.row for i in ranked if i is not item])
                _hold_as_copy(ctx, item, others, dest)


def _size(row: dict[str, Any]) -> str:
    size = row.get("size_bytes")
    return f"{int(size):,} bytes" if isinstance(size, (int, float)) else "size unknown"


def _hold_as_copy(ctx: SkillContext, item: PlanItem, others: list[dict[str, Any]], dest: str) -> None:
    """Escalate instead of filing, naming every same-name file (largest first) so a person can compare them."""
    row, where_to = item.row, ctx.folder_name(dest)
    others = sorted(others, key=lambda f: f.get("size_bytes") or 0, reverse=True)
    named = ", ".join(f"{o['id']} ({_size(o)})" for o in others)
    item.evidence = [*item.evidence, *(ev("possible_copy", f"{where_to} already has, or is also getting, a file named "
                                          f"{o.get('filename')} ({o['id']})", None, o["id"]) for o in others)]
    item.action, item.original_id, item.basis = "escalate", others[0]["id"], SAME_NAME_BASIS
    item.missing = [f"a person to say whether {row['id']} ({_size(row)}, score {item.score}) is a copy of {named}; "
                    f"all are named {row.get('filename')} and would sit in {where_to}"]


def _note(ctx: SkillContext, item: PlanItem, from_name: str) -> str:
    """The provenance note appended to the file's description (never replacing it)."""
    stamp, dest = f"{PROVENANCE_MARKER} {ctx.today()}]", ctx.folder_name(item.to_folder)
    source = one_line(from_name, NAME_LIMIT)  # STRIDE T2: folder names are platform text written into the row
    signals = ", ".join(sorted({e.signal for e in item.evidence}))
    return f"{stamp} Moved {source} -> {dest}. Evidence: {signals}. Score: {item.score} (threshold {ctx.rules.threshold})."


def _apply_move(ctx: SkillContext, item: PlanItem, folder: dict[str, Any]) -> None:
    row, label = item.row, item.row.get("filename")
    desc = row.get("description") or ""
    changes: dict[str, Any] = {"folder_id": item.to_folder, "description": (desc + "\n" if desc else "") + _note(ctx, item, folder["name"])}
    # The row must be exactly as planned: same folder, same description (we append to it), same updated_at.
    expect = {"folder_id": folder["id"], "description": row.get("description"), "updated_at": row.get("updated_at")}
    try:
        written = ctx.guard.update_file(row["id"], changes, expect=expect)
    except StaleRow as err:
        ctx.record(skill="triage_folder", action="skip_changed", status="skipped", target_id=row["id"], target_label=label,
                   details={"reason": str(err), "now_in": ctx.folder_name(err.current.get("folder_id"))})
        return
    except (WriteBlocked, WriteNotConfirmed, McpError) as err:
        ctx.record(skill="triage_folder", action="move", status="failed", target_id=row["id"], target_label=label,
                   details={"error": error_text(err), "to_folder_id": item.to_folder,
                            "write_sent": _write_sent(ctx, row["id"])})
        return
    details = {"from_folder_id": folder["id"], "to_folder_id": item.to_folder, "changes": {k: v for k, v in changes.items() if k != "description"}}
    if written.get("concurrent_change"):  # STRIDE T11: noted on the applied record; the move itself did land
        details["concurrent_change"] = sorted(written["concurrent_change"])
    ctx.record(skill="triage_folder", action="move", status="applied",
               target_id=row["id"], target_label=label, confidence="strong" if item.score >= ctx.rules.threshold + 2 else "medium",
               evidence=item.evidence, details=details)


def _escalate(ctx: SkillContext, row: dict[str, Any], why: str, kind: str, missing: list[str],
              person: str | None = None, party_id: str | None = None) -> DecisionRecord:
    """Escalate; a failure is recorded, never raised, so every later file still gets its outcome (STRIDE T3)."""
    try:
        return ctx.escalator.escalate(row, why, kind, missing, person, party_id)
    except BudgetExceeded:
        raise
    except Exception as err:  # STRIDE T3: any failure, not only a platform error; outside those two it may have been sent
        sent = False if isinstance(err, (McpError, WriteBlocked)) else "uncertain"
        return ctx.record(skill="escalate", action="escalate", status="failed", target_id=row["id"],
                          target_label=row.get("filename"), details={"error": error_text(err), "write_sent": sent})


def _write_sent(ctx: SkillContext, file_id: str) -> bool | str:
    """True if our update reached the platform, "uncertain" if the call errored (it may have landed), else False."""
    sent = [w for w in ctx.guard.writes if w.get("id") == file_id and w.get("tool") == "FileAttachment.update"]
    if any(not w.get("uncertain") for w in sent):
        return True
    return "uncertain" if sent else False


def run(ctx: SkillContext, args: dict[str, Any]) -> dict[str, Any]:
    folder = folder_named(ctx.folders(), args.get("folder_name") or "Incoming")
    if not folder:
        ctx.record(skill="triage_folder", action="folder_not_found", status="refused", target_label=args.get("folder_name"))
        return {"answer_text": f"There is no folder named {args.get('folder_name')!r}. Nothing was changed."}
    ctx.files(refresh=True)  # STRIDE T3: a second triage in one run must plan from what is there now, not from our own old read
    plan = build_plan(ctx, folder, args.get("file"))
    if args.get("file") and not plan:
        return {"answer_text": f"No file named {args['file']!r} is in {folder['name']}. Nothing was changed."}
    for item in plan:
        _record_plan(ctx, item, folder)
    for item in plan:
        if item.action == "leave":
            continue
        if item.row["id"] not in ctx.allowlist:  # outside this run's scope: no write AND no (permanent) escalation
            ctx.record(skill="triage_folder", action="out_of_scope", status="skipped", target_id=item.row["id"],
                       target_label=item.row.get("filename"),
                       details={"reason": OUT_OF_SCOPE, "planned": item.action, "to_folder_id": item.to_folder})
            continue
        if ctx.guard.can_write and item.action == "move" and item.to_folder:
            _apply_move(ctx, item, folder)
        elif item.action != "move":
            done = _escalate(ctx, item.row, _why(item), "insufficient_evidence", item.missing, item.person, item.party_id)
            if done.action == "cannot_escalate":  # the platform has no escalation tool: the answer must say so
                ctx.record(skill="triage_folder", action="not_escalated", status="skipped", target_id=item.row["id"],
                           target_label=item.row.get("filename"), details={"error": done.details.get("error")})
            elif done.status == "failed":  # STRIDE T3: say so in the triage outcome, which the answer is built from
                ctx.record(skill="triage_folder", action="escalation_not_confirmed", status="failed", target_id=item.row["id"],
                           target_label=item.row.get("filename"),
                           details={"error": f"the escalation was not confirmed: {done.details.get('error')}",
                                    "write_sent": done.details.get("write_sent")})
    return _summary(ctx, plan, folder)


def _also_changed(done: DecisionRecord | None) -> str:
    """STRIDE T11: say so when another seat changed other fields of the file while we wrote it."""
    fields = (done.details.get("concurrent_change") if done else None) or []
    return f" Another seat changed its {', '.join(fields)} at the same moment; check the file by hand." if fields else ""


def _why(item: PlanItem) -> str:
    if item.basis == SAME_NAME_BASIS:
        return f"Possible copy of {item.original_id}: filing it would put two files with the same name in one folder."
    if _is_possible_copy(item):  # decision C: one text for a hash + size + name and a name + size match
        return f"Possible copy of {item.original_id}: left where it is, not filed, moved or archived."
    return {"refuse": "Cannot identify this file: no linked record, no sender, no readable contents.",
            "escalate": "Not enough evidence to file this safely.",
            "conflict": "The signals disagree about where this file belongs."}.get(item.action, "Needs a human decision.")


def _record_plan(ctx: SkillContext, item: PlanItem, folder: dict[str, Any]) -> None:
    status = {"move": "planned", "leave": "skipped"}.get(item.action, "refused")
    ctx.record(skill="triage_folder", action=f"plan_{item.action}", status=status, target_id=item.row["id"],
               target_label=item.row.get("filename"), confidence="strong" if item.score >= ctx.rules.threshold + 2 else ("medium" if item.action == "move" else "none"),
               evidence=item.evidence, missing=item.missing,
               details={"from_folder_id": folder["id"], "to_folder_id": item.to_folder, "score": item.score,
                        "person": item.person, "person_source": item.person_source,
                        "duplicate_of": item.original_id, "duplicate_basis": item.basis})


def _summary(ctx: SkillContext, plan: list[PlanItem], folder: dict[str, Any]) -> dict[str, Any]:
    applied = ctx.guard.can_write
    mode = "applied" if applied else "plan only - nothing was changed"
    outcome = {r.target_id: r for r in ctx.records.all()
               if r.skill == "triage_folder" and r.status in ("applied", "skipped", "failed") and not r.action.startswith("plan_")}
    lines = [f"Triage of '{folder['name']}' ({mode}):"]
    for item in plan:
        name, fid = item.row.get("filename"), item.row["id"]
        done = outcome.get(fid)
        if done and done.action == "out_of_scope":
            lines.append(f"- {name} ({fid}) left for a person: not in this run's scope ({OUT_OF_SCOPE}); "
                         "nothing was written or escalated for it.")
        elif done and done.action == "not_escalated":
            who = f" Ask: {item.person}." if item.person else ""
            lines.append(f"- {name} ({fid}) not filed ({item.action}): missing {', '.join(item.missing)}. Left where it "
                         f"is for a person, NOT escalated: {done.details.get('error')}.{who}")
        elif done and done.status == "skipped":
            lines.append(f"- SKIPPED {name} ({fid}): it changed since the plan (now in {done.details.get('now_in')}); not overwritten.")
        elif done and done.status == "failed":
            lines.append(f"- FAILED {name} ({fid}): {done.details.get('error')}")
        elif item.action == "move":
            verb = "moved to" if applied else "->"
            lines.append(f"- {name} ({fid}) {verb} {ctx.folder_name(item.to_folder)} (score {item.score}).{_also_changed(done)}")
        elif item.action == "leave":
            lines.append(f"- {name} ({fid}) left alone: it is already archived.")
        else:
            who = f" Ask: {item.person}." if item.person else ""
            lines.append(f"- {name} ({fid}) not filed ({item.action}): missing {', '.join(item.missing)}.{who}")
    return {"folder": folder["name"], "mode": mode, "answer_text": "\n".join(lines),
            "plan": [{"id": i.row["id"], "filename": i.row.get("filename"), "action": i.action,
                      "to_folder": ctx.folder_name(i.to_folder) if i.to_folder else None, "score": i.score,
                      "missing": i.missing, "person": i.person} for i in plan]}


SKILL = Skill(
    name="triage_folder",
    description=("Tidy a folder (default Incoming): score each file on independent evidence, plan a destination, "
                 "and refuse or escalate files it cannot justify. Writes only in apply mode, only to allow-listed files. "
                 "Pass `file` to triage one file."),
    input_schema={"type": "object", "properties": {
        "folder_name": {"type": "string", "description": "Folder to tidy, default Incoming"},
        "file": {"type": "string", "description": "Optional exact filename to triage on its own"}}},
    run=run,
)
