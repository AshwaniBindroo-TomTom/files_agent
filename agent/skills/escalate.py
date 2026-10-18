"""A9: routed, de-duplicated escalations.

An escalation needs an AgentSession, so one is created per run (apply mode only).
Escalations can't point at a file, so the subject carries the file id and is
also the de-duplication key. Only this seat's own escalations count for it.
Keystone has no assignable people, so escalations are unassigned; the person
to ask is named in the reason. If the platform does not offer the escalation tool,
nothing is sent: the file is recorded as cannot_escalate and left where it is.
"""
from __future__ import annotations

from typing import Any

from agent.auth import AuthError
from agent.budget import BudgetExceeded
from agent.config import ESCALATION_TOOL
from agent.guards import WriteBlocked
from agent.mcp_client import McpError
from agent.records import DecisionRecord
from agent.safe_reads import list_all
from agent.skills.common import SkillContext, error_text, ev
from agent.textsafe import one_line

REASON_CODES = {"insufficient_evidence": "other", "out_of_seat": "policy_refusal"}
PERSON_LIMIT = 200  # a name (at most 100) plus its provenance label
REASON_LIMIT = 1500


def subject_for(file_row: dict[str, Any]) -> str:
    return f"[files-agent] {file_row['id']} {file_row.get('filename', '')}".strip()


class Escalator:
    def __init__(self, ctx: SkillContext) -> None:
        self.ctx = ctx
        self._session_id: str | None = None
        self._session_uncertain = False
        self._subjects: dict[str, dict[str, Any]] | None = None  # this seat's escalations: subject -> {id, created_by}
        self._foreign: dict[str, dict[str, Any]] = {}  # the same, created by anyone else
        self._uncertain: set[str] = set()  # subjects whose create errored: may have landed, never resent

    def _me(self) -> str | None:
        return (self.ctx.me() or {}).get("id")

    def _existing_subjects(self) -> dict[str, dict[str, Any]]:
        if self._subjects is None:
            self._subjects, self._foreign = self._load()
        return self._subjects

    def _load(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Split every escalation into this seat's and everyone else's, by the server-set created_by."""
        me, own, foreign = self._me(), {}, {}
        rows = list_all(self.ctx.mcp, "AgentEscalation.list")
        for row in rows:
            entry = {"id": row.get("id"), "created_by": row.get("created_by")}
            # STRIDE S1: the subject is public; only an escalation this seat created may suppress a new one
            (own if me and row.get("created_by") == me else foreign).setdefault(row.get("subject") or "", entry)
        unknown = sum(1 for row in rows if not row.get("created_by"))
        if not me or unknown:  # counted as someone else's, so they never suppress an escalation
            self.ctx.trace.write("escalation_owner_unknown", our_id_known=bool(me), rows_without_creator=unknown)
        return own, foreign

    def _session(self) -> str:
        if self._session_uncertain:  # STRIDE T3: an AgentSession.create errored and may have landed; never send another
            raise WriteBlocked("the session create failed and may have landed, so no other session is created in this run")
        if self._session_id is None:
            title = f"Files Agent (team20) run {self.ctx.today()} {self.ctx.run_id}"
            args: dict[str, Any] = {"title": title, "actor_label": "Files Agent (team20)"}
            if self.ctx.settings.actor_kind:
                args["actor_kind"] = self.ctx.settings.actor_kind
            try:
                session_id = (self.ctx.guard.create("AgentSession.create", args) or {}).get("id")
                if not session_id:
                    raise McpError("AgentSession.create returned no id", data_code="bad_reply", own=True)
            except (BudgetExceeded, WriteBlocked):  # refused before anything was sent
                raise
            except McpError:
                self._session_uncertain = True
                raise
            except Exception as err:  # STRIDE T3: e.g. the journal save failed after the create was sent
                self._session_uncertain = True
                raise McpError(f"AgentSession.create may have landed ({type(err).__name__})", data_code="client_error",
                               own=True) from err
            self._session_id = session_id
            self.ctx.note_ids(session_id)
            # STRIDE R3: the run's session is a permanent row; record it so the run file ties it to this run.
            # Its id stays in details: an id in the trail is a cited id, and sessions are not in the id check's lists.
            self.ctx.record(skill="escalate", action="session_created", status="applied", target_label=title,
                            details={"session_id": session_id})
        return self._session_id

    def escalate(self, file_row: dict[str, Any], why: str, kind: str, missing: list[str],
                 person: str | None = None, party_id: str | None = None) -> DecisionRecord:
        """Record (plan mode) or create one escalation. A platform or guard failure becomes a record, never an
        exception; only a budget stop still ends the run."""
        subject = subject_for(file_row)
        person = one_line(person, PERSON_LIMIT) or None  # STRIDE T2: other teams can edit the names this comes from
        base = dict(skill="escalate", target_id=file_row["id"], target_label=file_row.get("filename"),
                    missing=missing, details={"subject": subject, "person": person, "reason": why})
        if file_row["id"] not in self.ctx.allowlist:  # escalations are permanent: same scope as file writes
            return self.ctx.record(action="out_of_scope", status="skipped", **base)
        if ESCALATION_TOOL not in self.ctx.catalog.names:  # dropped by the platform: say so, in plan mode too
            return self._record(base, "cannot_escalate", "skipped", write_sent=False,
                                error=f"this seat has no {ESCALATION_TOOL} tool, so no person was asked")
        if subject in self._uncertain:  # STRIDE T3: an earlier create of it errored and may have landed
            return self._record(base, "escalate", "skipped", write_sent="uncertain",
                                error="an earlier create of this escalation errored; not resent in this run")
        try:
            existing = self._existing_subjects()
        except (McpError, AuthError) as err:
            return self._record(base, "escalate", "failed", write_sent=False,
                                error=f"could not list escalations: {error_text(err)}")
        if subject in existing:
            return self._record(base, "already_escalated", "skipped", existing_escalation_id=existing[subject]["id"],
                                created_by=existing[subject]["created_by"])
        lookalike = self._foreign.get(subject)
        extra = {"lookalike_escalation_id": lookalike["id"]} if lookalike else {}
        if lookalike:
            self.ctx.trace.write("foreign_escalation_subject", subject=subject, escalation_id=lookalike["id"],
                                 created_by=lookalike["created_by"])
        if not self.ctx.guard.can_write:
            return self._record(base, "escalate", "planned", **extra)
        reason = why + (f" Missing: {', '.join(missing)}." if missing else "") + (f" Person to ask: {person}." if person else "")
        return self._create(base, subject, one_line(reason, REASON_LIMIT), kind, party_id, extra)

    def _create(self, base: dict[str, Any], subject: str, reason: str, kind: str, party_id: str | None,
                extra: dict[str, Any]) -> DecisionRecord:
        try:
            session_id = self._session()
        except (McpError, WriteBlocked) as err:
            return self._record(base, "escalate", "failed", write_sent=False, error=f"no session: {error_text(err)}",
                                **extra)
        args: dict[str, Any] = {"session_id": session_id, "subject": subject, "reason": reason,
                                "reason_code": REASON_CODES.get(kind, "other")}
        if party_id:
            args["party_id"] = party_id
        try:
            created = self.ctx.guard.create(ESCALATION_TOOL, args)
        except WriteBlocked as err:
            return self._record(base, "escalate", "failed", write_sent=False, error=str(err), **extra)
        except BudgetExceeded:  # refused before anything was sent
            raise
        except Exception as err:  # STRIDE T3: an error reply, a malformed one or a failed journal save can follow a create
            created = self._read_back(subject)
            if created is None:  # it may still have landed, so it is never sent again in this run
                self._uncertain.add(subject)
                return self._record(base, "escalate", "failed", write_sent="uncertain", error=error_text(err),
                                    session_id=session_id, **extra)
        escalation_id = (created or {}).get("id")
        self._existing_subjects()[subject] = {"id": escalation_id, "created_by": self._me()}
        return self.ctx.record(action="escalate", status="escalated",
                               evidence=[ev("escalation", subject, source_id=escalation_id)],
                               **{**base, "details": {**base["details"], "escalation_id": escalation_id,
                                                      "session_id": session_id, **extra}})

    def _read_back(self, subject: str) -> dict[str, Any] | None:
        """After an unclear create: did it land? Only a row this seat created with this subject counts."""
        try:
            own, _foreign = self._load()
        except (McpError, AuthError):
            return None
        return own.get(subject)

    def _record(self, base: dict[str, Any], action: str, status: str, **details: Any) -> DecisionRecord:
        return self.ctx.record(action=action, status=status, **{**base, "details": {**base["details"], **details}})
