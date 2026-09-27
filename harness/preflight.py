"""Pre-flight (T4.9): refuse a live write run if the platform drifted from the fixture.

On Keystone the write allow-list is pinned to the 9 original Incoming files. Those 9
must be in Incoming, unchanged since the fixture and still tagged 'untriaged'.
Any other row in Incoming can never be written (the guard refuses it and triage
leaves it for a person), so it is a WARNING, not a reason to refuse, provided the
fixture already knew it and it has not changed since. An unknown or changed extra
stays a PROBLEM: duplicate grouping reads every file, so a new row could change how
the 9 are judged and the live expectations would no longer hold. For the same reason,
a row ANYWHERE that is new, changed or gone since the fixture and shares a name (as
duplicate grouping compares names) or a recorded hash with one of the 9 is a PROBLEM,
and so is a fixture row that has left Incoming.
"""
from __future__ import annotations

import sys
from typing import Any

from agent.config import KEYSTONE_INCOMING_ALLOWLIST, WRITE_BUSINESS
from agent.safe_reads import folder_named, folders_by_id, list_all
from agent.skills.duplicates import stem


class PreflightFailed(Exception):
    pass


def _drift(file_id: str, row: dict[str, Any], base: dict[str, Any] | None, need_untriaged: bool) -> str | None:
    """The first way this Incoming row differs from the fixture, or None."""
    if base is None:
        return f"{file_id} is not in the fixture"
    if row.get("updated_at") != base.get("updated_at"):
        return f"{row.get('filename')} changed since the fixture (updated_at {row.get('updated_at')})"
    if row.get("folder_id") != base.get("folder_id"):
        return f"{row.get('filename')} moved into Incoming since the fixture"
    if need_untriaged and "untriaged" not in (row.get("tags") or ""):
        return f"{row.get('filename')} is no longer tagged 'untriaged'"
    return None


def assess(rt: Any, fixture: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(problems, warnings). Any problem refuses the run; warnings are reported and allowed."""
    problems = list(rt.catalog.check_required())
    warnings: list[str] = []
    fixture_hash = (fixture.get("_manifest") or {}).get("tool_hash")
    if not fixture_hash:
        problems.append("the fixture manifest has no tool hash: re-capture fixtures")
    elif fixture_hash != rt.catalog.hash():
        problems.append(f"tool catalogue changed (fixture {fixture_hash}, live {rt.catalog.hash()}): re-capture fixtures")
    incoming = folder_named(folders_by_id(rt.admin_mcp), "Incoming")
    if not incoming:
        return problems + ["no Incoming folder"], warnings
    live_rows = {r["id"]: r for r in list_all(rt.admin_mcp, "FileAttachment.list", folder_id=incoming["id"])}
    required = KEYSTONE_INCOMING_ALLOWLIST if rt.settings.business == WRITE_BUSINESS else frozenset(live_rows)
    missing = sorted(required - set(live_rows))
    if missing:
        problems.append(f"allow-listed file(s) missing from Incoming: {missing}")
    baseline = {r["id"]: r for r in fixture["tables"]["FileAttachment"]}
    for file_id, row in sorted(live_rows.items()):
        in_scope = file_id in required
        drift = _drift(file_id, row, baseline.get(file_id), need_untriaged=in_scope)
        if drift:
            problems.append(drift if in_scope else f"{drift} (outside the allow-list, but the live expectations assume the fixture's Incoming)")
        elif not in_scope:
            warnings.append(f"{row.get('filename')} ({file_id}) is in Incoming but outside the write allow-list: "
                            "it will not be written or escalated")
    problems += _outside_incoming(rt, baseline, live_rows, required, incoming["id"])
    return problems, warnings


def _outside_incoming(rt: Any, baseline: dict[str, dict[str, Any]], live_rows: dict[str, dict[str, Any]],
                      required: frozenset[str], incoming_id: str) -> list[str]:
    """Changes elsewhere that can still change how the allow-listed files are judged."""
    watched = [baseline[i] for i in required if i in baseline]
    names = {stem(r.get("filename", "")) for r in watched}
    hashes = {r["content_hash"] for r in watched if r.get("content_hash")}

    def related(row: dict[str, Any]) -> bool:
        return stem(row.get("filename", "")) in names or row.get("content_hash") in hashes

    everywhere = {r["id"]: r for r in list_all(rt.admin_mcp, "FileAttachment.list")}
    problems = []
    for file_id, base in sorted(baseline.items()):
        if file_id in required or file_id in live_rows:
            continue
        if base.get("folder_id") == incoming_id:
            problems.append(f"{base.get('filename')} ({file_id}) has left Incoming since the fixture")
        elif related(base) and file_id not in everywhere:
            problems.append(f"{base.get('filename')} ({file_id}) is gone since the fixture and shares a name or hash "
                            "with an allow-listed file")
    for file_id, row in sorted(everywhere.items()):
        if file_id in required or file_id in live_rows or not related(row):
            continue
        base = baseline.get(file_id)
        if base is None or any(row.get(k) != base.get(k) for k in ("updated_at", "folder_id")):
            problems.append(f"{row.get('filename')} ({file_id}) is new or changed since the fixture and shares a name "
                            "or hash with an allow-listed file")
    return problems


def check(rt: Any, fixture: dict[str, Any]) -> list[str]:
    """Problems found (empty list = safe to run)."""
    return assess(rt, fixture)[0]


def require_ok(rt: Any, fixture: dict[str, Any]) -> list[str]:
    """Raise PreflightFailed on any problem; otherwise return the warnings (also traced and printed)."""
    problems, warnings = assess(rt, fixture)
    rt.trace.write("preflight", ok=not problems, problems=problems, warnings=warnings)
    if warnings:
        print("pre-flight warnings (not blocking):\n  - " + "\n  - ".join(warnings), file=sys.stderr)
    if problems:
        raise PreflightFailed("Pre-flight failed; nothing was written:\n  - " + "\n  - ".join(problems))
    return warnings
