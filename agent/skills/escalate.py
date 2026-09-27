"""A9: routed, de-duplicated escalations.

An escalation needs an AgentSession, so one is created per run (apply mode only).
Escalations can't point at a file, so the subject carries the file id and is
also the de-duplication key. Keystone has no assignable people, so escalations
are unassigned; the person to ask is named in the reason.
"""
from __future__ import annotations

from typing import Any

from agent.records import DecisionRecord
from agent.safe_reads import list_all
from agent.skills.common import SkillContext, ev

REASON_CODES = {"insufficient_evidence": "other", "out_of_seat": "policy_refusal", "needs_permission": "other"}


def subject_for(file_row: dict[str, Any]) -> str:
    return f"[files-agent] {file_row['id']} {file_row.get('filename', '')}".strip()


class Escalator:
    def __init__(self, ctx: SkillContext) -> None:
        self.ctx = ctx
        self._session_id: str | None = None
        self._subjects: set[str] | None = None

    def _existing_subjects(self) -> set[str]:
        if self._subjects is None:
            self._subjects = {r.get("subject") or "" for r in list_all(self.ctx.mcp, "AgentEscalation.list")}
        return self._subjects

    def _session(self) -> str:
        if self._session_id is None:
            args: dict[str, Any] = {"title": f"Files Agent (team20) run {self.ctx.today()}", "actor_label": "Files Agent (team20)"}
            if self.ctx.settings.actor_kind:
                args["actor_kind"] = self.ctx.settings.actor_kind
            self._session_id = self.ctx.guard.create("AgentSession.create", args)["id"]
        return self._session_id

    def escalate(self, file_row: dict[str, Any], why: str, kind: str, missing: list[str],
                 person: str | None = None, party_id: str | None = None) -> DecisionRecord:
        subject = subject_for(file_row)
        base = dict(skill="escalate", target_id=file_row["id"], target_label=file_row.get("filename"),
                    missing=missing, details={"subject": subject, "person": person, "reason": why})
        if file_row["id"] not in self.ctx.allowlist:  # escalations are permanent: same scope as file writes
            return self.ctx.record(action="out_of_scope", status="skipped", **base)
        if subject in self._existing_subjects():
            return self.ctx.record(action="already_escalated", status="skipped", **base)
        reason = why + (f" Missing: {', '.join(missing)}." if missing else "") + (f" Person to ask: {person}." if person else "")
        if not self.ctx.guard.can_write:
            return self.ctx.record(action="escalate", status="planned", **base)
        args: dict[str, Any] = {"session_id": self._session(), "subject": subject, "reason": reason,
                                "reason_code": REASON_CODES.get(kind, "other")}
        if party_id:
            args["party_id"] = party_id
        created = self.ctx.guard.create("AgentEscalation.create", args)
        self._existing_subjects().add(subject)
        return self.ctx.record(action="escalate", status="escalated",
                               evidence=[ev("escalation", subject, source_id=(created or {}).get("id"))],
                               **{**base, "details": {**base["details"], "escalation_id": (created or {}).get("id")}})
