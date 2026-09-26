"""A2/A3/A4/A6/A7: evidence-scored triage of a folder, planned first, applied only in apply mode.

Scoring rules live in agent/filing_rules.toml (TEAM-OWNED). A description such as
"Belongs in HR" counts only when another, independent signal points to the same
folder; if it points somewhere else the file is flagged as a conflict, never moved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.guards import StaleRow, WriteBlocked, WriteNotConfirmed
from agent.mcp_client import McpError
from agent.records import Evidence
from agent.safe_reads import find_files_by_name, folder_named, where
from agent.skills.common import Skill, SkillContext, ev, uploader_of
from agent.skills.duplicates import find_groups
from agent.skills.profiles import (PROVENANCE_MARKER, default_folder_name, description_destination,
                                   doc_type_of, similar_file_folder)


@dataclass
class PlanItem:
    row: dict[str, Any]
    action: str  # move | duplicate | conflict | escalate | refuse | leave
    to_folder: str | None = None
    score: int = 0
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    person: str | None = None
    party_id: str | None = None
    original_id: str | None = None
    basis: str | None = None  # how a duplicate was matched


TRUSTED_DUPLICATE_BASIS = "recorded hash"


def _signals(ctx: SkillContext, row: dict[str, Any], files: list[dict[str, Any]], skip: set[str]) -> list[Evidence]:
    rules, folders = ctx.rules, ctx.folders()
    out: list[Evidence] = []
    dtype = doc_type_of(row.get("filename", ""), rules)
    dest = None
    if dtype:
        similar = similar_file_folder(row, dtype, files, rules, skip)
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
        who = row.get("_party_id_display") or row.get("from_name") or row.get("from_email")
        out.append(ev("sender", f"sent by {who}", None, row.get("party_id")))
    return out


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
    groups = {c["id"]: g for g in find_groups(files) for c in g.copies}
    plan = [_plan_item(ctx, row, files, skip, groups.get(row["id"]))
            for row in sorted(in_folder, key=lambda r: r.get("filename", ""))]
    for item in plan:
        if item.action == "duplicate":
            _follow_original(item, plan, folder)
        if item.action in ("escalate", "refuse", "conflict"):
            row = item.row
            item.person = (row.get("_party_id_display") or row.get("from_name") or row.get("from_email")) or uploader_of(ctx, row["id"])
            item.party_id = row.get("party_id")
    return plan


def _plan_item(ctx: SkillContext, row: dict[str, Any], files: list[dict[str, Any]], skip: set[str], group: Any) -> PlanItem:
    if row.get("is_archived"):
        return PlanItem(row, "leave", missing=["nothing: it is already archived, so it was left as it is"])
    item = _score(ctx, PlanItem(row, "refuse", evidence=_signals(ctx, row, files, skip)))
    if group is None:
        return item
    original = group.original
    if group.basis.startswith(TRUSTED_DUPLICATE_BASIS):
        return PlanItem(row, "duplicate", original_id=original["id"], evidence=item.evidence,
                        to_folder=original.get("folder_id"), basis=group.basis)
    # Name + size alone is a suspicion, not proof: never archive on it, ask a person instead.
    hint = ev("suspected_duplicate", f"same name and size as {original.get('filename')}; no trusted hash", None, original["id"])
    return PlanItem(row, "escalate", evidence=[*item.evidence, hint], original_id=original["id"], basis=group.basis,
                    missing=[f"confirmation that it duplicates {original.get('filename')} ({original['id']}); "
                             f"only {group.basis} match"])


def _follow_original(item: PlanItem, plan: list[PlanItem], folder: dict[str, Any]) -> None:
    """A duplicate goes where its original goes; if the original isn't filed, the duplicate waits for a person."""
    twin = next((p for p in plan if p.row["id"] == item.original_id), None)
    if twin is not None:
        item.to_folder = twin.to_folder if twin.action == "move" else None
    if item.to_folder in (None, folder["id"]):
        item.action, item.to_folder = "escalate", None
        item.missing = [f"a filed original: {item.original_id} is not filed yet, so the duplicate was not archived"]


def _note(ctx: SkillContext, item: PlanItem, from_name: str) -> str:
    """The provenance note appended to the file's description (never replacing it)."""
    stamp, dest = f"{PROVENANCE_MARKER} {ctx.today()}]", ctx.folder_name(item.to_folder)
    if item.action == "duplicate":
        return (f"{stamp} Archived as a duplicate of {item.original_id} (matched on {item.basis}; not byte-verified) "
                f"and moved {from_name} -> {dest}, next to the original.")
    signals = ", ".join(sorted({e.signal for e in item.evidence}))
    return f"{stamp} Moved {from_name} -> {dest}. Evidence: {signals}. Score: {item.score} (threshold {ctx.rules.threshold})."


def _apply_move(ctx: SkillContext, item: PlanItem, folder: dict[str, Any]) -> None:
    row, label = item.row, item.row.get("filename")
    desc = row.get("description") or ""
    changes: dict[str, Any] = {"folder_id": item.to_folder, "description": (desc + "\n" if desc else "") + _note(ctx, item, folder["name"])}
    if item.action == "duplicate":
        changes["is_archived"] = True
    # The row must be exactly as planned: same folder, same description (we append to it), same updated_at.
    expect = {"folder_id": folder["id"], "description": row.get("description"), "updated_at": row.get("updated_at")}
    try:
        ctx.guard.update_file(row["id"], changes, expect=expect)
    except StaleRow as err:
        ctx.record(skill="triage_folder", action="skip_changed", status="skipped", target_id=row["id"], target_label=label,
                   details={"reason": str(err), "now_in": ctx.folder_name(err.current.get("folder_id"))})
        return
    except (WriteBlocked, WriteNotConfirmed, McpError) as err:
        ctx.record(skill="triage_folder", action="move", status="failed", target_id=row["id"], target_label=label,
                   details={"error": str(err), "to_folder_id": item.to_folder,
                            "write_sent": _write_sent(ctx, row["id"])})
        return
    ctx.record(skill="triage_folder", action="archive_duplicate" if item.action == "duplicate" else "move", status="applied",
               target_id=row["id"], target_label=label, confidence="strong" if item.score >= ctx.rules.threshold + 2 else "medium",
               evidence=item.evidence, details={"from_folder_id": folder["id"], "to_folder_id": item.to_folder, "changes": {k: v for k, v in changes.items() if k != "description"}})
    if item.action == "duplicate":
        ctx.escalator.escalate(row, f"Duplicate of {item.original_id}; archived, needs someone with delete rights to remove it.",
                               "needs_permission", ["delete permission"], party_id=row.get("party_id"))


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
    plan = build_plan(ctx, folder, args.get("file"))
    if args.get("file") and not plan:
        return {"answer_text": f"No file named {args['file']!r} is in {folder['name']}. Nothing was changed."}
    for item in plan:
        _record_plan(ctx, item, folder)
    for item in plan:
        if item.action == "leave":
            continue
        if ctx.guard.can_write and item.action in ("move", "duplicate") and item.to_folder:
            _apply_move(ctx, item, folder)
        elif item.action not in ("move", "duplicate"):
            ctx.escalator.escalate(item.row, _why(item), "insufficient_evidence", item.missing, item.person, item.party_id)
    return _summary(ctx, plan, folder)


def _why(item: PlanItem) -> str:
    if item.action == "escalate" and item.original_id:
        return "Possible duplicate that I could not confirm or file safely."
    return {"refuse": "Cannot identify this file: no linked record, no sender, no readable contents.",
            "escalate": "Not enough evidence to file this safely.",
            "conflict": "The signals disagree about where this file belongs."}.get(item.action, "Needs a human decision.")


def _record_plan(ctx: SkillContext, item: PlanItem, folder: dict[str, Any]) -> None:
    status = {"move": "planned", "duplicate": "planned", "leave": "skipped"}.get(item.action, "refused")
    ctx.record(skill="triage_folder", action=f"plan_{item.action}", status=status, target_id=item.row["id"],
               target_label=item.row.get("filename"), confidence="strong" if item.score >= ctx.rules.threshold + 2 else ("medium" if item.action == "move" else "none"),
               evidence=item.evidence, missing=item.missing,
               details={"from_folder_id": folder["id"], "to_folder_id": item.to_folder, "score": item.score,
                        "person": item.person, "duplicate_of": item.original_id, "duplicate_basis": item.basis})


def _summary(ctx: SkillContext, plan: list[PlanItem], folder: dict[str, Any]) -> dict[str, Any]:
    applied = ctx.guard.can_write
    mode = "applied" if applied else "plan only - nothing was changed"
    outcome = {r.target_id: r for r in ctx.records.all()
               if r.skill == "triage_folder" and r.status in ("applied", "skipped", "failed")}
    lines = [f"Triage of '{folder['name']}' ({mode}):"]
    for item in plan:
        name, fid = item.row.get("filename"), item.row["id"]
        done = outcome.get(fid)
        if done and done.status == "skipped":
            lines.append(f"- SKIPPED {name} ({fid}): it changed since the plan (now in {done.details.get('now_in')}); not overwritten.")
        elif done and done.status == "failed":
            lines.append(f"- FAILED {name} ({fid}): {done.details.get('error')}")
        elif item.action == "move":
            verb = "moved to" if applied else "->"
            lines.append(f"- {name} ({fid}) {verb} {ctx.folder_name(item.to_folder)} (score {item.score}).")
        elif item.action == "duplicate":
            what = "archived with a pointer and moved to" if applied else "archive it with a pointer and move it to"
            lines.append(f"- {name} ({fid}) is a duplicate of {item.original_id} per {item.basis} "
                         f"(not byte-verified): {what} {ctx.folder_name(item.to_folder)}; removal needs someone with delete rights.")
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
