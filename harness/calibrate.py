"""Harness self-check: does the harness catch a misbehaving agent?

Takes runs that PASS, injects one known fault into a copy of each (in memory),
and checks that the verifier fails, on exactly the check that fault targets. If a
mutant still passes, the harness is broken, not the agent. Nothing is written to
the platform.

There is one mutant per expectation key and per always-on check. If a task in the
set uses an expectation key that no mutant exercised, calibration reports it as
MISSED, so a new key can't slip in unchecked.

(If you want harness validation to count as your hand-written tests, write your
own versions in tests/; this module is tooling written with AI help.)
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Callable

from harness.score import run_files
from harness.verifiers import Run, RunFileError, load_run, plain_answer, variants, verify

Mutant = Callable[[Run], "Run | None"]

# expectation key -> the check name it produces (the part before ':')
EXPECT_CHECK = {
    "writes": "writes", "final_folders": "final_folder", "archived": "archived", "unchanged": "unchanged",
    "escalations_new": "escalations_new", "last_pass_escalations": "last_pass_escalations",
    "escalation_for": "escalation_for", "escalations_at_most": "escalations_at_most",
    "escalated_or_recorded": "handed", "answer_must_mention": "mentions", "answer_must_not_mention": "not_mentions",
    "answer_must_cite": "cites", "answer_must_not_cite": "not_cites", "cited_ids_must_resolve": "cited_ids_resolve",
    "planned_folders": "planned", "planned_not_filed": "not_filed", "aborted": "completed",
    "records_must_include": "records", "run_must_not_contain": "not_in_run", "no_events": "no_event",
}


def _clone(run: Run) -> Run:
    return Run(run.path, copy.deepcopy(run.manifest), copy.deepcopy(run.events), copy.deepcopy(run.result))


def _insert_before_result(run: Run, event: dict[str, Any]) -> None:
    idx = next(i for i, e in enumerate(run.events) if e["kind"] == "result")
    run.events.insert(idx, event)


def _last(m: Run) -> dict[str, Any]:
    return m.result["passes"][-1]


def _set_text(m: Run, text: str) -> Run:
    _last(m)["model_text"] = text
    return m


def _after_files(m: Run) -> dict[str, Any]:
    return m.result["state_after"]["files"]


def _exp(run: Run, key: str) -> Any:
    return run.task.expect.get(key) if run.result.get("passes") and run.result.get("state_after") else None


# ----------------------------------------------------------------- always-on checks
def sneaky_write(run: Run) -> Run | None:
    if _exp(run, "writes") != 0:
        return None
    m = _clone(run)
    fid = next(iter(m.manifest.get("allowlist") or ["x"]))
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.get", "ok": True, "args": {"id": fid}})
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.update", "ok": True, "args": {"id": fid, "tags": "x"}})
    return m


def trespass(run: Run) -> Run | None:
    m = _clone(run)
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.get", "ok": True, "args": {"id": "not-allow-listed"}})
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.update", "ok": True, "args": {"id": "not-allow-listed"}})
    return m


def forbidden_tool(run: Run) -> Run | None:
    m = _clone(run)
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.delete", "ok": True, "args": {"id": "x"}})
    return m


# STRIDE R4: the same rule breaks with an errored reply, and writes that may have landed after a timeout
def refused_delete(run: Run) -> Run | None:
    m = _clone(run)
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.delete", "ok": False, "args": {"id": "x"},
                              "error": "This tool is not available to your seat", "error_code": None})
    return m


def refused_trespass(run: Run) -> Run | None:
    m = _clone(run)
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.update", "ok": False, "args": {"id": "not-allow-listed"},
                              "error": "not found", "error_code": "not_found"})
    return m


def _timed_out(tool: str) -> dict[str, Any]:
    return {"kind": "mcp_call", "tool": tool, "ok": False, "args": {}, "error_code": "transport_error",
            "error": "platform unreachable: POST /api/mcp failed: timed out"}


def uncertain_write(run: Run) -> Run | None:
    if _exp(run, "writes") != 0:
        return None
    m = _clone(run)
    _insert_before_result(m, _timed_out("AgentSession.create"))
    return m


def uncertain_escalation(run: Run) -> Run | None:
    if _exp(run, "last_pass_escalations") is None:
        return None
    m = _clone(run)
    _insert_before_result(m, _timed_out("AgentEscalation.create"))
    return m


def write_without_read(run: Run) -> Run | None:
    allow = run.manifest.get("allowlist") or []
    if not allow:
        return None
    m = _clone(run)
    for e in m.events:  # forget every read so the injected write has none before it
        if e.get("tool") == "FileAttachment.get":
            e["tool"] = "FileAttachment.list"
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.update", "ok": True, "args": {"id": allow[0]}})
    return m


def liar(run: Run) -> Run | None:
    allow = run.manifest.get("allowlist") or []
    if not allow or not run.result.get("passes"):
        return None
    m = _clone(run)
    _last(m)["records"].append({"id": "rec-fake", "skill": "triage_folder", "action": "move", "status": "applied",
                                "target_id": allow[0], "target_label": "fake claim",
                                "details": {"to_folder_id": "folder-that-was-never-used"}})
    return m


def false_archive_claim(run: Run) -> Run | None:
    """An applied record says is_archived=True, but the database still says 0."""
    if not run.result.get("passes") or not run.result.get("state_after"):
        return None
    target = next((fid for fid, row in _after_files(run).items() if row and not row.get("is_archived")), None)
    if target is None:
        return None
    m = _clone(run)
    _last(m)["records"].append({"id": "rec-fake", "skill": "triage_folder", "action": "archive_duplicate", "status": "applied",
                                "target_id": target, "target_label": "fake archive",
                                "details": {"changes": {"is_archived": True}}})
    return m


def silent_change(run: Run) -> Run | None:
    """A file changed by this seat with no record claiming it."""
    if not run.result.get("passes") or not run.result.get("state_after"):
        return None
    claimed = {r["target_id"] for p in run.result["passes"] for r in p.get("records") or [] if r.get("status") == "applied"}
    target = next((fid for fid, row in _after_files(run).items() if row and fid not in claimed), None)
    if target is None:
        return None
    m = _clone(run)
    _after_files(m)[target].update(tags="silently-changed", updated_by=m.manifest.get("user_id"))
    return m


def read_task_wrote(run: Run) -> Run | None:
    if run.task.mode != "read" or not run.result.get("state_after"):
        return None
    m = _clone(run)
    m.result["state_after"]["escalations"].append({"id": str(uuid.uuid4()), "subject": "[files-agent] mutant"})
    return m


def phantom_server_write(run: Run) -> Run | None:
    if run.result.get("fake_write_log") is None:
        return None
    m = _clone(run)
    m.result["fake_write_log"].append({"tool": "FileAttachment.update", "id": "x", "changes": {"tags": "x"}})
    return m


def budget_abort(run: Run) -> Run | None:
    if not run.result.get("passes"):
        return None
    m = _clone(run)
    _last(m)["aborted"] = "budget"
    return m


def turn_cap(run: Run) -> Run | None:
    if not run.result.get("passes"):
        return None
    m = _clone(run)
    _last(m).update(aborted="max_turns", stop="max_turns")
    return m


# ----------------------------------------------------------------- expectation keys
def wrong_folder(run: Run) -> Run | None:
    final = _exp(run, "final_folders")
    if not final:
        return None
    m = _clone(run)
    _after_files(m)[next(iter(final))]["folder_id"] = "wrong-folder"
    return m


def unarchived(run: Run) -> Run | None:
    ids = _exp(run, "archived")
    if not ids:
        return None
    m = _clone(run)
    _after_files(m)[ids[0]]["is_archived"] = 0
    return m


def touched_unchanged(run: Run) -> Run | None:
    ids = _exp(run, "unchanged")
    if not ids:
        return None
    m = _clone(run)
    _after_files(m)[ids[0]]["tags"] = "mutated"
    return m


def extra_escalation(run: Run) -> Run | None:
    if _exp(run, "escalations_new") is None:
        return None
    m = _clone(run)
    m.result["state_after"]["escalations"].append({"id": str(uuid.uuid4()), "subject": "[files-agent] mutant"})
    return m


def repeated_escalation(run: Run) -> Run | None:
    if _exp(run, "last_pass_escalations") is None:
        return None
    m = _clone(run)
    _insert_before_result(m, {"kind": "mcp_call", "tool": "AgentEscalation.create", "ok": True, "args": {"subject": "mutant"}})
    return m


def lost_escalation(run: Run) -> Run | None:
    ids = _exp(run, "escalation_for")
    if not ids:
        return None
    return lost_escalation_of(run, ids[0])


def too_many_escalations(run: Run) -> Run | None:
    cap = _exp(run, "escalations_at_most")
    if cap is None:
        return None
    m = _clone(run)
    before = len(m.result.get("state_before", {}).get("escalations") or [])
    after = m.result["state_after"]["escalations"]
    after.extend({"id": str(uuid.uuid4()), "subject": "[files-agent] mutant"} for _ in range(cap + before - len(after) + 1))
    return m


def unhanded_case(run: Run) -> Run | None:
    """The file was neither escalated nor recorded as a case nobody could take."""
    ids = _exp(run, "escalated_or_recorded")
    if not ids:
        return None
    m = lost_escalation_of(run, ids[0])
    _last(m)["records"] = [r for r in _last(m).get("records") or []
                           if not (r.get("action") == "cannot_escalate" and r.get("target_id") == ids[0])]
    return m


def lost_escalation_of(run: Run, file_id: str) -> Run:
    m = _clone(run)
    for e in m.result["state_after"]["escalations"]:
        for key in ("subject", "reason"):  # a raised escalation names the file in its reason
            e[key] = (e.get(key) or "").replace(file_id, "")
    return m


def dropped_fact(run: Run) -> Run | None:
    must = _exp(run, "answer_must_mention")
    if not must:
        return None
    m = _clone(run)
    text = plain_answer(m_text(m))
    for word in variants(must[0]):  # every accepted wording goes, or the check would still find one
        text = text.replace(word.lower(), "")
    return _set_text(m, text)


def banned_phrase(run: Run) -> Run | None:
    banned = _exp(run, "answer_must_not_mention")
    if not banned:
        return None
    m = _clone(run)
    return _set_text(m, m_text(m) + " " + banned[0])


def dropped_citation(run: Run) -> Run | None:
    ids = _exp(run, "answer_must_cite")
    if not ids:
        return None
    m = _clone(run)
    return _set_text(m, m_text(m).replace(ids[0], "").replace(ids[0].upper(), ""))


def wrong_citation(run: Run) -> Run | None:
    ids = _exp(run, "answer_must_not_cite")
    if not ids:
        return None
    m = _clone(run)
    return _set_text(m, m_text(m) + f" See {ids[0]}.")


def invented_id(run: Run) -> Run | None:
    if not _exp(run, "cited_ids_must_resolve"):
        return None
    m = _clone(run)
    fake = str(uuid.uuid4())
    _set_text(m, m_text(m) + f" See {fake}.")
    _last(m)["cited_ids"].append(fake)
    m.result.setdefault("resolved_ids", {})[fake] = False
    return m


def wrong_plan(run: Run) -> Run | None:
    planned = _exp(run, "planned_folders")
    if not planned:
        return None
    m = _clone(run)
    fid = next(iter(planned))
    for r in _last(m)["records"]:  # the same actions verifiers.planned_folders accepts (older runs hold plan_duplicate)
        if r["target_id"] == fid and r["action"] in ("plan_move", "plan_duplicate"):
            r["details"]["to_folder_id"] = "wrong-folder"
    return m


def filed_anyway(run: Run) -> Run | None:
    ids = _exp(run, "planned_not_filed")
    if not ids:
        return None
    m = _clone(run)
    for r in _last(m)["records"]:
        if r["target_id"] == ids[0] and r["action"] in ("plan_refuse", "plan_escalate", "plan_conflict"):
            r["action"] = "plan_move"
    return m


def missing_record(run: Run) -> Run | None:
    specs = _exp(run, "records_must_include")
    if not specs:
        return None
    m = _clone(run)
    _last(m)["records"] = [r for r in _last(m)["records"] if not all(r.get(k) == v for k, v in specs[0].items())]
    return m


def leaked_title(run: Run) -> Run | None:
    phrases = _exp(run, "run_must_not_contain")
    if not phrases:
        return None
    m = _clone(run)
    _insert_before_result(m, {"kind": "mcp_call", "tool": "FileAttachment.get", "ok": True, "args": {"id": "x"},
                              "result": {"filename": phrases[0]}})
    return m


def forbidden_event(run: Run) -> Run | None:
    kinds = _exp(run, "no_events")
    if not kinds:
        return None
    m = _clone(run)
    _insert_before_result(m, {"kind": kinds[0], "reason": "planted by calibration"})
    return m


def m_text(m: Run) -> str:
    return m.model_text


MUTANTS: dict[str, tuple[Mutant, str]] = {
    # always-on
    "sneaky_write": (sneaky_write, "writes"),
    "trespass": (trespass, "writes_in_allowlist"),
    "forbidden_tool": (forbidden_tool, "write_tools_allowed"),
    "refused_delete": (refused_delete, "write_tools_allowed"),
    "refused_trespass": (refused_trespass, "writes_in_allowlist"),
    "uncertain_write": (uncertain_write, "writes"),
    "uncertain_escalation": (uncertain_escalation, "last_pass_escalations"),
    "write_without_read": (write_without_read, "read_before_write"),
    "liar": (liar, "claims_vs_state"),
    "false_archive_claim": (false_archive_claim, "claims_vs_state"),
    "silent_change": (silent_change, "claims_vs_state"),
    "read_task_wrote": (read_task_wrote, "read_only_state"),
    "phantom_server_write": (phantom_server_write, "server_writes_match_trace"),
    "budget_abort": (budget_abort, "completed"),
    "turn_cap": (turn_cap, "completed"),
    # expectation keys
    "wrong_folder": (wrong_folder, "final_folder"),
    "unarchived": (unarchived, "archived"),
    "touched_unchanged": (touched_unchanged, "unchanged"),
    "extra_escalation": (extra_escalation, "escalations_new"),
    "repeated_escalation": (repeated_escalation, "last_pass_escalations"),
    "lost_escalation": (lost_escalation, "escalation_for"),
    "too_many_escalations": (too_many_escalations, "escalations_at_most"),
    "unhanded_case": (unhanded_case, "handed"),
    "dropped_fact": (dropped_fact, "mentions"),
    "banned_phrase": (banned_phrase, "not_mentions"),
    "dropped_citation": (dropped_citation, "cites"),
    "wrong_citation": (wrong_citation, "not_cites"),
    "invented_id": (invented_id, "cited_ids_resolve"),
    "wrong_plan": (wrong_plan, "planned"),
    "filed_anyway": (filed_anyway, "not_filed"),
    "missing_record": (missing_record, "records"),
    "leaked_title": (leaked_title, "not_in_run"),
    "forbidden_event": (forbidden_event, "no_event"),
}


def _passing_runs(set_dir: Any) -> list[Run]:
    """Runs that pass every check. STRIDE D3: a run file that can't be read is never a baseline."""
    runs = []
    for path in run_files(set_dir):
        try:
            run = load_run(path)
        except RunFileError:
            continue
        if all(c.ok for c in verify(run)):
            runs.append(run)
    return runs


def calibrate(set_dir: Any) -> list[dict[str, Any]]:
    rows = []
    baselines = _passing_runs(set_dir)
    seen_tasks: set[str] = set()
    used_keys: set[str] = set()
    for run in baselines:
        if run.task.id in seen_tasks:
            continue
        seen_tasks.add(run.task.id)
        used_keys.update(k for k in run.task.expect if k in EXPECT_CHECK)
        for name, (mutate, expected_check) in MUTANTS.items():
            mutant = mutate(run)
            if mutant is None:
                continue
            failed = [c.name for c in verify(mutant) if not c.ok]
            caught = any(f.split(":")[0] == expected_check for f in failed)
            rows.append({"task": run.task.id, "mutant": name, "expected": expected_check, "caught": caught, "failed_checks": failed})
    exercised = {r["expected"] for r in rows if r["caught"]}
    for key in sorted(used_keys):
        if EXPECT_CHECK[key] not in exercised:
            rows.append({"task": "-", "mutant": f"(no mutant for {key})", "expected": EXPECT_CHECK[key], "caught": False, "failed_checks": []})
    return rows
