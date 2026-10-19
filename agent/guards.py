"""Write guard (T2.2). Every write the agent makes goes through here; the one exception is
restore (agent/snapshot.py), which puts snapshot values back through the admin client.

- Plan-only is the default: nothing is written unless mode == "apply".
- On the live platform, nothing is written until a write journal is attached.
- File writes only to allow-listed row ids, and only the fields in UPDATE_FIELDS.
- Escalations only about allow-listed files (the subject names the file).
- Pre-read: the row must still look the way the plan expected (else StaleRow).
  Callers pass `updated_at` too, so any change by anyone since the plan skips the row.
- Permission check: `_permissions.write` and `_readonly_fields` from the row itself.
- Post-read: confirm the change landed; a mismatch means someone overwrote us. A field we did NOT
  send that differs between the pre-read and the post-read was changed by someone else inside our
  write window: it is traced and returned as `concurrent_change` (the platform has no compare-and-set).
- Every write is recorded once its call returns or fails in any way (Ctrl-C included), before
  the confirming read, so neither a failed call nor a failed confirm can hide a write that happened.
  A file write also records the values it replaced, so restore can put back exactly those.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from agent.config import ESCALATION_WRITE_TOOLS, WRITE_TOOLS
from agent.mcp_client import McpError, safe_error_text
from agent.textsafe import one_line
from agent.trace import Trace

# The only fields the agent ever changes on a file (triage: move, and a note appended to the description).
# Decision C: it never archives; a possible copy is escalated, not archived. Restore follows (RESTORE_FIELDS).
UPDATE_FIELDS = frozenset({"folder_id", "description"})
# Every file field a seat can write (snapshot and restore use the same set).
WRITABLE_FIELDS = ("folder_id", "filename", "tags", "description", "is_archived", "party_id", "entity_type", "entity_id")
ESCALATION_SUBJECT = re.compile(r"\[files-agent\] (\S+)")


class WriteBlocked(Exception):
    """A write was refused before anything was sent."""


class StaleRow(Exception):
    """The row changed since it was planned; the write was skipped."""

    def __init__(self, message: str, current: dict[str, Any]) -> None:
        super().__init__(message)
        self.current = current


class WriteNotConfirmed(Exception):
    """The post-write read does not show our change (possible clobber)."""


class WriteGuard:
    def __init__(self, mcp: Any, trace: Trace, mode: str, allowlist: frozenset[str], live: bool = False) -> None:
        if mode not in ("plan", "apply"):
            raise ValueError("mode must be 'plan' or 'apply'")
        self.mcp = mcp
        self.trace = trace
        self.mode = mode
        self.allowlist = allowlist
        self.live = live
        self.writes: list[dict[str, Any]] = []
        self.journal: Callable[[dict[str, Any]], None] | None = None

    @property
    def can_write(self) -> bool:
        return self.mode == "apply"

    def _reserve(self, calls: int) -> None:
        budget = getattr(self.mcp, "budget", None)
        if budget is not None:
            budget.reserve(calls)

    def _require_apply(self, what: str) -> None:
        if not self.can_write:
            self.trace.write("write_blocked", reason="plan-only", what=what)
            raise WriteBlocked(f"plan-only mode: {what} not sent")
        if self.live and self.journal is None:  # STRIDE E1: a live write needs the harness's journal (and restore)
            self.trace.write("write_blocked", reason="no write journal", what=what)
            raise WriteBlocked(f"live write without a write journal: {what} not sent (run it through the harness)")

    def _block(self, reason: str, message: str, **details: Any) -> WriteBlocked:
        self.trace.write("write_blocked", reason=reason, **details)
        return WriteBlocked(message)

    def update_file(self, file_id: str, changes: dict[str, Any], expect: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_apply(f"update {file_id}")
        if file_id not in self.allowlist:
            raise self._block("not allow-listed", f"{file_id} is not in the write allow-list", file_id=file_id)
        self._reserve(3)  # read, write, confirm: never start a write the budget would cut off
        before = self.mcp.call("FileAttachment.get", {"id": file_id})
        stale = {k: (v, before.get(k)) for k, v in (expect or {}).items() if not same_value(before.get(k), v)}
        if stale:
            self.trace.write("stale_row", file_id=file_id, changed=stale)
            raise StaleRow(f"{one_line(before.get('filename'))} changed since the plan: {stale}", before)
        if not (before.get("_permissions") or {}).get("write"):
            raise self._block("no write permission", f"no write permission on {file_id}", file_id=file_id)
        readonly = set(before.get("_readonly_fields") or []) & set(changes)
        if readonly:
            raise self._block("read-only field", f"read-only fields {sorted(readonly)} on {file_id}", file_id=file_id)
        extra = sorted(set(changes) - UPDATE_FIELDS)
        if extra:  # STRIDE E2: an 'id' (or any other field) in `changes` must never reach the platform
            raise self._block("field not allowed", f"fields {extra} may not be written by this agent", file_id=file_id,
                              fields=extra)
        # STRIDE T9: keep the values we replace, so restore puts back an edit made after the snapshot.
        entry = {"tool": "FileAttachment.update", "id": file_id, "changes": changes,
                 "before": {k: before.get(k) for k in changes}}
        self._send("FileAttachment.update", {**changes, "id": file_id}, entry)
        try:
            after = self.mcp.call("FileAttachment.get", {"id": file_id})
        except McpError as err:
            self.trace.write("write_not_confirmed", file_id=file_id, error=str(err))
            raise WriteNotConfirmed(f"the write was sent but the confirming read failed ({safe_error_text(err)}), "
                                    "so it is unconfirmed") from err
        lost = {k: v for k, v in changes.items() if not same_value(after.get(k), v)}
        if lost:
            self.trace.write("write_not_confirmed", file_id=file_id, lost=lost)
            raise WriteNotConfirmed(f"our change did not stick: {', '.join(sorted(lost))} no longer hold our values "
                                    "(another seat changed the file right after our write); left as it is now")
        # STRIDE T11: partial detection only; a change to a field we sent, inside the same window, can't be seen
        drift = {k: {"before": before.get(k), "after": after.get(k)} for k in WRITABLE_FIELDS
                 if k not in changes and not same_value(before.get(k), after.get(k))}
        if drift:
            self.trace.write("concurrent_change", file_id=file_id, changed=drift)
        return {"before": before, "after": after, "concurrent_change": drift}

    def create(self, tool: str, args: dict[str, Any]) -> Any:
        """Create an AgentSession or raise/create an escalation (the only other writes allowed)."""
        self._require_apply(tool)
        if tool not in WRITE_TOOLS or tool == "FileAttachment.update":
            raise WriteBlocked(f"{tool} is not an allowed create tool")
        if tool in ESCALATION_WRITE_TOOLS:
            self._check_escalation_scope(args)
        self._reserve(1)
        result = self._send(tool, args, {"tool": tool, "args": args})
        return result

    def _check_escalation_scope(self, args: dict[str, Any]) -> None:
        """STRIDE E2: escalations are permanent, so the file the subject names must be allow-listed. raise has no
        subject, so there the reason starts with it."""
        match = ESCALATION_SUBJECT.match(str(args.get("subject") or args.get("reason") or ""))
        about = match.group(1) if match else None
        if about not in self.allowlist:
            raise self._block("escalation outside the allow-list", f"escalation about {about} is outside the write "
                              "allow-list", subject=str(args.get("subject") or args.get("reason"))[:120])

    def _send(self, tool: str, args: dict[str, Any], entry: dict[str, Any]) -> Any:
        """Send one write and log it. If the call fails in any way, the platform may still have applied it, so it
        is logged as `uncertain`: restore and the records then treat it as possibly ours, never as nothing."""
        try:
            result = self.mcp.call(tool, args)
        except BaseException:  # STRIDE T1: IncompleteRead, a malformed reply or Ctrl-C can follow a write that landed
            self._log_write({**entry, "uncertain": True})
            raise
        created = result.get("id") if isinstance(result, dict) else None
        self._log_write({**entry, "id": entry.get("id") or created})
        return result

    def _log_write(self, entry: dict[str, Any]) -> None:
        """Record a write as soon as its call ends; the journal (live runs) saves it to disk for restore."""
        self.writes.append(entry)
        if self.journal is not None:
            self.journal(entry)


def same_value(a: Any, b: Any) -> bool:
    """Compare field values the way the platform stores them (is_archived comes back as 0/1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    return a == b
