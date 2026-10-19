"""A9: routed, de-duplicated escalations.

Escalations can't point at a file, so the subject "[files-agent] <file id> <filename>"
carries the file id and is also the de-duplication key. Only this seat's own
escalations count for it. The person to ask is named in the reason.

Two platform tools (the catalogue decides, see Catalog.escalation_tool; create while it exists):
- endpoint.agent_governance.escalations.raise (Keystone since 15 Oct 2026). It hands the
  case to a named person and stops the agent on its session, so each case gets its own
  AgentSession. It has no subject, so the reason starts with it. The person must come from
  escalations.assignees; when that lists no one (Keystone: assignees.noLinkedSignIns),
  the case is recorded as cannot_escalate and nothing is sent, which the course staff
  confirmed is the expected behaviour.
- AgentEscalation.create (before 15 Oct): one AgentSession per run, unassigned.
With neither tool, nothing is sent either: cannot_escalate, and the file stays where it is.
"""
from __future__ import annotations

from typing import Any

from agent.auth import AuthError
from agent.budget import BudgetExceeded
from agent.config import ASSIGNEES_TOOL, ESCALATION_TOOL, RAISE_TOOL
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


def escalation_label(row: dict[str, Any]) -> str:
    """The subject of a created escalation, or the reason of a raised one (which starts with the subject)."""
    return str(row.get("subject") or row.get("reason") or "")


def _find(rows: dict[str, dict[str, Any]], subject: str) -> dict[str, Any] | None:
    """This subject's entry: an exact subject, or a raised reason that starts with it."""
    return rows.get(subject) or next((v for k, v in rows.items() if k.startswith(subject + ": ")), None)


def _escalation_id(reply: Any) -> str | None:
    """The new escalation's id from a create reply ({id}) or a raise reply ({status, result: {...}})."""
    if not isinstance(reply, dict):
        return None
    inner = reply.get("result") if isinstance(reply.get("result"), dict) else {}
    nested = inner.get("escalation") if isinstance(inner.get("escalation"), dict) else {}
    return reply.get("id") or inner.get("escalation_id") or inner.get("id") or nested.get("id")


class Escalator:
    def __init__(self, ctx: SkillContext) -> None:
        self.ctx = ctx
        self._session_id: str | None = None
        self._session_uncertain = False
        self._subjects: dict[str, dict[str, Any]] | None = None  # this seat's escalations: subject -> {id, created_by}
        self._foreign: dict[str, dict[str, Any]] = {}  # the same, created by anyone else
        self._uncertain: set[str] = set()  # subjects whose create errored: may have landed, never resent
        self._assignees: list[dict[str, Any]] | None = None  # raise only: the people a case can go to

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
            (own if me and row.get("created_by") == me else foreign).setdefault(escalation_label(row), entry)
        unknown = sum(1 for row in rows if not row.get("created_by"))
        if not me or unknown:  # counted as someone else's, so they never suppress an escalation
            self.ctx.trace.write("escalation_owner_unknown", our_id_known=bool(me), rows_without_creator=unknown)
        return own, foreign

    def _session(self, case: str | None = None) -> str:
        """The run's session (create), or a new one for this case (raise stops the agent on the session it is given)."""
        if self._session_uncertain:  # STRIDE T3: an AgentSession.create errored and may have landed; never send another
            raise WriteBlocked("the session create failed and may have landed, so no other session is created in this run")
        if self._session_id is None or case:
            title = f"Files Agent (team20) run {self.ctx.today()} {self.ctx.run_id}" + (f" case {case}" if case else "")
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
            if not case:
                self._session_id = session_id
            self.ctx.note_ids(session_id)
            # STRIDE R3: the run's session is a permanent row; record it so the run file ties it to this run.
            # Its id stays in details: an id in the trail is a cited id, and sessions are not in the id check's lists.
            self.ctx.record(skill="escalate", action="session_created", status="applied", target_label=title,
                            details={"session_id": session_id})
            return session_id
        return self._session_id

    def _assignee(self, party_id: str | None) -> dict[str, Any] | None:
        """raise only: the file's own party if the platform lists it as assignable, else the first person listed,
        else None (read once per run)."""
        if self._assignees is None:
            reply = self.ctx.mcp.call(ASSIGNEES_TOOL, {})
            result = (reply or {}).get("result") if isinstance(reply, dict) else None
            options = (result or {}).get("options") if isinstance(result, dict) else None
            self._assignees = [o for o in options or [] if isinstance(o, dict) and (o.get("party_id") or o.get("id"))]
            self.ctx.trace.write("escalation_assignees", count=len(self._assignees),
                                 reason_code=(result or {}).get("reason_code") if isinstance(result, dict) else None)
        ids = [str(o.get("party_id") or o.get("id")) for o in self._assignees]
        if not ids:
            return None
        chosen = party_id if party_id in ids else ids[0]
        return {"party_id": chosen, "matched_file": chosen == party_id}

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
        tool = self.ctx.catalog.escalation_tool()
        if tool is None:  # dropped by the platform: say so, in plan mode too
            return self._record(base, "cannot_escalate", "skipped", write_sent=False,
                                error=f"this seat has no escalation tool ({RAISE_TOOL} or {ESCALATION_TOOL}), "
                                      "so no person was asked")
        if subject in self._uncertain:  # STRIDE T3: an earlier create of it errored and may have landed
            return self._record(base, "escalate", "skipped", write_sent="uncertain",
                                error="an earlier create of this escalation errored; not resent in this run")
        try:
            existing = self._existing_subjects()
        except (McpError, AuthError) as err:
            return self._record(base, "escalate", "failed", write_sent=False,
                                error=f"could not list escalations: {error_text(err)}")
        if (found := _find(existing, subject)) is not None:
            return self._record(base, "already_escalated", "skipped", existing_escalation_id=found["id"],
                                created_by=found["created_by"])
        assignee = None
        if tool == RAISE_TOOL:
            try:
                assignee = self._assignee(party_id)
            except (McpError, AuthError) as err:
                return self._record(base, "escalate", "failed", write_sent=False,
                                    error=f"could not list who can take escalations: {error_text(err)}")
            if assignee is None:  # the course staff: with no assignee, record the case and stop
                return self._record(base, "cannot_escalate", "skipped", write_sent=False,
                                    error="the platform lists no person who can take an escalation "
                                          "(escalations.assignees has no options), so the case is recorded here instead")
        lookalike = _find(self._foreign, subject)
        extra = {"lookalike_escalation_id": lookalike["id"]} if lookalike else {}
        if lookalike:
            self.ctx.trace.write("foreign_escalation_subject", subject=subject, escalation_id=lookalike["id"],
                                 created_by=lookalike["created_by"])
        if assignee:
            extra["assignee_party_id"] = assignee["party_id"]
        if not self.ctx.guard.can_write:
            return self._record(base, "escalate", "planned", **extra)
        reason = why + (f" Missing: {', '.join(missing)}." if missing else "") + (f" Person to ask: {person}." if person else "")
        return self._create(base, subject, one_line(reason, REASON_LIMIT), kind, party_id, extra, tool, assignee)

    def _create(self, base: dict[str, Any], subject: str, reason: str, kind: str, party_id: str | None,
                extra: dict[str, Any], tool: str = ESCALATION_TOOL, assignee: dict[str, Any] | None = None) -> DecisionRecord:
        try:
            session_id = self._session(base["target_id"] if tool == RAISE_TOOL else None)
        except (McpError, WriteBlocked) as err:
            return self._record(base, "escalate", "failed", write_sent=False, error=f"no session: {error_text(err)}",
                                **extra)
        if tool == RAISE_TOOL:  # no subject field: the reason starts with it, so the de-duplication still works
            args: dict[str, Any] = {"session_id": session_id, "assignee_party_id": (assignee or {}).get("party_id"),
                                    "reason": one_line(f"{subject}: {reason}", REASON_LIMIT),
                                    "reason_code": REASON_CODES.get(kind, "other")}
        else:
            args = {"session_id": session_id, "subject": subject, "reason": reason,
                    "reason_code": REASON_CODES.get(kind, "other")}
            if party_id:
                args["party_id"] = party_id
        try:
            created = self.ctx.guard.create(tool, args)
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
        escalation_id = _escalation_id(created)
        if escalation_id is None and tool == RAISE_TOOL:  # the raise reply's shape is not documented: read it back
            escalation_id = (self._read_back(subject) or {}).get("id")
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
        return _find(own, subject)

    def _record(self, base: dict[str, Any], action: str, status: str, **details: Any) -> DecisionRecord:
        return self.ctx.record(action=action, status=status, **{**base, "details": {**base["details"], **details}})
