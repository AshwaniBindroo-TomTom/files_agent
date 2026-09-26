"""Boundary and refusal skills.

- explain_access (A10): refuse requests outside the seat, citing allowed_apps.
- remove_file:  refuse delete/trash, citing the row's own permissions.
- file_contents: refuse to "read" a file; the platform stores no bytes. Never invents content.
- list_files (A11): list files, withholding rows owned by apps this seat can't open.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from agent.privacy import is_withheld
from agent.safe_reads import find_files_by_name
from agent.skills.common import Skill, SkillContext, ev, uploader_of
from agent.skills.duplicates import find_groups

APP_KEYWORDS = {
    "payroll": r"pay ?slips?|salar(y|ies)|payroll|net pay|pay run",
    "esign": r"\be-?sign(ature)?\b|signature request|\bsigning\b",  # \b: "design" is not "e-sign"
    "contracts": r"\bcontracts?\b|\bclauses?\b",
    "accounting": r"invoices?|\bbills?\b|ledger|journal entr|tax liability",
    "manufacturing": r"work orders?|\bbom\b|routing|job cards?",
    "inventory": r"stock levels?|warehouses?|stock entr",
    "designreview": r"design (file|review)s?|\bcad\b",
    "email": r"mailbox|inbox|email campaign",
    "support": r"helpdesk|support tickets?",
}


def detect_app(text: str) -> str | None:
    return next((app for app, pat in APP_KEYWORDS.items() if re.search(pat, text, re.IGNORECASE)), None)


def explain_access(ctx: SkillContext, args: dict[str, Any]) -> dict[str, Any]:
    request = str(args.get("request", ""))
    allowed = list(ctx.me().get("allowed_apps") or [])
    app = str(args.get("app") or detect_app(request) or "")
    if app and app not in allowed:
        ctx.record(skill="explain_access", action="refuse_out_of_seat", status="refused", target_label=app,
                   evidence=[ev("allowed_apps", ", ".join(allowed)), ev("requested_app", app)],
                   missing=[f"access to the {app} app"])
        return {"allowed": False, "app": app, "allowed_apps": allowed,
                "answer_text": (f"I can't help with that: it needs the {app} app, and this seat (Files Agent) only has "
                                f"{', '.join(allowed)}. Ask the {app} seat, an EA or an administrator.")}
    ctx.record(skill="explain_access", action="within_seat" if app else "no_app_detected", status="info", target_label=app or None,
               evidence=[ev("allowed_apps", ", ".join(allowed))])
    return {"allowed": True, "app": app or None, "allowed_apps": allowed,
            "answer_text": f"This seat has the apps: {', '.join(allowed)}." + (f" {app} is one of them." if app else "")}


def resolve_file(ctx: SkillContext, text: str) -> list[dict[str, Any]]:
    """Every file the text could mean. More than one match is the caller's problem:
    with duplicated filenames on the platform, silently picking one misleads."""
    files = ctx.files()
    exact = find_files_by_name(files, text.strip())
    if exact:
        return exact
    if "duplicate" in text.lower():
        words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", text) if w.lower() not in {"the", "duplicate", "file", "copy", "delete", "remove"}]
        copies = [c for g in find_groups(files) for c in g.copies]
        return [c for c in copies if all(w in (c.get("filename") or "").lower() for w in words)]
    return []


def _ambiguous(ctx: SkillContext, skill: str, target: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [r["id"] for r in rows]
    ctx.note_ids(*ids)
    ctx.record(skill=skill, action="file_not_identified", status="refused", target_label=target,
               missing=["an exact file id: the name matches more than one file"],
               details={"candidates": ids})
    listing = "; ".join(f"{r.get('filename')} ({r['id']})" for r in rows)
    return {"answer_text": (f"{len(rows)} files match {target!r}: {listing}. I couldn't identify exactly one file, "
                            "so I did nothing. Say which id you mean.")}


def remove_file(ctx: SkillContext, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("file", ""))
    rows = resolve_file(ctx, target)
    if not rows:
        ctx.record(skill="remove_file", action="file_not_identified", status="refused", target_label=target, missing=["an exact filename"])
        return {"answer_text": f"I couldn't identify exactly one file from {target!r}, so I did nothing."}
    if len(rows) > 1:
        return _ambiguous(ctx, "remove_file", target, rows)
    row = rows[0]
    ctx.note_ids(row["id"])
    can_delete = bool((row.get("_permissions") or {}).get("delete"))
    trash_tools = sorted(n for n in ctx.catalog.names if n.endswith((".delete", ".trash")) and n.startswith("FileAttachment"))
    ctx.record(skill="remove_file", action="refuse_remove", status="refused", target_id=row["id"], target_label=row.get("filename"),
               evidence=[ev("permission", f"_permissions.delete = {can_delete}"), ev("tools", f"delete/trash tools available: {trash_tools or 'none'}")],
               missing=["delete permission for this seat"])
    return {"removed": False, "file_id": row["id"],
            "answer_text": (f"I can't delete {row.get('filename')} ({row['id']}): this seat has no delete permission on it "
                            f"(_permissions.delete = {can_delete}) and no delete or trash tool. Nothing was changed; "
                            "someone with delete rights has to remove it.")}


def file_contents(ctx: SkillContext, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("file", ""))
    rows = resolve_file(ctx, target)
    if not rows:
        ctx.record(skill="file_contents", action="file_not_identified", status="refused", target_label=target)
        return {"answer_text": f"No file is named exactly {target!r}. I did not guess."}
    # A shared filename refuses every match, so the reader sees all of them, not a silent pick.
    lines = [f"{len(rows)} files are named {rows[0].get('filename')}; none can be read."] if len(rows) > 1 else []
    for row in rows:
        ctx.note_ids(row["id"])
        uploader = uploader_of(ctx, row["id"])
        facts = [f"description: {row.get('description')!r}" if row.get("description") else "no description",
                 f"tags: {row.get('tags')!r}" if row.get("tags") else "no tags",
                 f"uploader per access log: {uploader}" if uploader else "no upload record"]
        ctx.record(skill="file_contents", action="refuse_read_contents", status="refused", target_id=row["id"], target_label=row.get("filename"),
                   evidence=[ev("storage", "no file revisions / bytes are stored for this row")], missing=["file contents"])
        lines.append(f"I can't read {row.get('filename')} ({row['id']}): the platform stores no file contents for it, "
                     f"so there is nothing to quote. What the record itself holds: {'; '.join(facts)}.")
    return {"answer_text": "\n".join(lines)}


def list_files(ctx: SkillContext, args: dict[str, Any]) -> dict[str, Any]:
    files = ctx.files()
    withheld = [f for f in files if is_withheld(f, ctx.catalog.can_list)]
    shown = [f for f in files if f not in withheld]
    by_folder = Counter(ctx.folder_name(f.get("folder_id")) for f in shown)
    hidden = Counter(f["entity_type"] for f in withheld)
    ctx.record(skill="list_files", action="list", status="info", details={"shown": len(shown), "withheld": dict(hidden)},
               evidence=[ev("leak_guard", f"{len(withheld)} rows belong to apps this seat can't open")])
    lines = [f"{len(shown)} files are visible to this seat:"] + [f"- {n}: {c}" for n, c in sorted(by_folder.items())]
    if withheld:
        lines.append(f"{len(withheld)} further rows were withheld because they belong to apps this seat can't open "
                     f"({', '.join(f'{t}: {n}' for t, n in hidden.items())}).")
    return {"shown": len(shown), "withheld": dict(hidden), "by_folder": dict(by_folder), "answer_text": "\n".join(lines)}


def _schema(**props: str) -> dict[str, Any]:
    return {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
            "required": list(props)[:1]}


SKILLS = [
    Skill("explain_access", "Check whether a request is inside this seat's apps; refuse and explain if not. Read-only.",
          _schema(request="The user's request, verbatim"), explain_access),
    Skill("remove_file", "Handle a request to delete, trash or remove a file: checks permissions and refuses; never deletes.",
          _schema(file="Exact filename, or e.g. 'duplicate PO'"), remove_file),
    Skill("file_contents", "Handle a request to read or quote a file: the platform stores no contents, so it refuses and lists what the record holds.",
          _schema(file="Exact filename"), file_contents),
    Skill("list_files", "List files by folder, withholding rows that belong to apps this seat can't open. Read-only.",
          {"type": "object", "properties": {}}, list_files),
]
