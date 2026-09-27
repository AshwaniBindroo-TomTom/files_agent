"""Runner (T1.8 / T4.2). Every run is written to disk before anything is scored.

One run = one repeat of one task, in a fresh environment (a fresh fake server,
or the live platform). A task with passes=2 asks its question twice in the same
environment (idempotency). The run file holds: the manifest (first line), every
event, and a final `result` event with the scoped database state before and after.

Guarantees:
- A run whose set-up fails still gets a run file (stub manifest + failed result).
- Live write runs are wrapped in pre-flight, snapshot, a write journal and a restore
  that runs even if collecting the after-state fails.
- Tasks that depend on the fake server (faults, extra files) never run live.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent import snapshot
from agent.config import KEYSTONE_INCOMING_ALLOWLIST, get_settings
from agent.loop import run_agent
from agent.mcp_client import McpError
from agent.redact import Redactor
from agent.runtime import WritesNotAllowed, build, check_write_permission, make_model, make_transport
from agent.safe_reads import list_all
from agent.trace import Trace
from harness import fixtures, preflight
from harness.manifest import build_manifest, stub_manifest
from harness.tasks import Task

STATE_FIELDS = (*snapshot.WRITABLE_FIELDS, "updated_at", "updated_by")
ID_TOOLS = ("Item.list", "DriveFolder.list", "Party.list", "AgentEscalation.list")


class RunRefused(Exception):
    """The task may not run like this; nothing was written."""


LiveApplyRefused = RunRefused  # older name


def scope_ids(task: Task, allowlist: frozenset[str]) -> list[str]:
    exp = task.expect
    ids = set(allowlist) | set(exp.get("final_folders", {})) | set(exp.get("archived", [])) | set(exp.get("unchanged", []))
    for fault in task.faults:  # the first id in a fault is a file; swap_archived names two files
        parts = fault.split(":")
        files = parts[1:3] if parts[0] == "swap_archived" else parts[1:2]
        ids.update(p for p in files if len(p) == 36)
    return sorted(ids)


def capture_state(rt: Any, ids: list[str]) -> dict[str, Any]:
    files = {}
    for file_id in ids:
        try:
            row = rt.admin_mcp.call("FileAttachment.get", {"id": file_id})
            files[file_id] = {f: row.get(f) for f in STATE_FIELDS}
        except McpError as err:
            if err.data_code == "transport_error":  # an outage must fail the run, not look like a missing file
                raise
            files[file_id] = None
    me = rt.session.me().get("id")
    escalations = [{"id": e["id"], "subject": e.get("subject"), "created_at": e.get("created_at")}
                   for e in list_all(rt.admin_mcp, "AgentEscalation.list") if e.get("created_by") == me]
    return {"files": files, "escalations": escalations}


def resolve_ids(rt: Any, ids: list[str]) -> dict[str, bool]:
    """Does every id in the answer exist on the platform? Checked now, stored in the run file."""
    known: set[str] | None = None
    out = {}
    for rid in ids:
        try:
            rt.admin_mcp.call("FileAttachment.get", {"id": rid})
            out[rid] = True
            continue
        except McpError:
            pass
        if known is None:
            known = set()
            for tool in (t for t in ID_TOOLS if t in rt.catalog.names):
                try:
                    known.update(r["id"] for r in list_all(rt.admin_mcp, tool))
                except McpError as err:
                    rt.trace.write("resolve_ids_warning", tool=tool, error=str(err))
        out[rid] = rid in known
    return out


def planned_runs(task: Task, *, target: str, repeat: int | None = None, live_apply: bool = False) -> int:
    """How many run files this task will produce. Raises RunRefused / WritesNotAllowed before anything happens."""
    if target == "live" and (task.faults or task.extra_files):
        raise RunRefused(f"{task.id} uses fake-server faults or extra files, so it runs offline only")
    live_write = target == "live" and task.mode == "apply"
    if live_write and not task.live_write:
        raise RunRefused(f"{task.id} is not marked live_write = true, so it never writes on the live platform "
                         "(only TI2L is: its escalations are permanent, and one live write run is planned)")
    if live_write and not live_apply:
        raise RunRefused(f"{task.id} writes; on the live platform it needs --live-apply and AS_ALLOW_WRITES=1")
    check_write_permission(target, "apply" if task.mode == "apply" else "plan", get_settings(task.business))
    return 1 if live_write else (repeat or task.repeat)


def run_task(task: Task, *, target: str, model_kind: str, set_dir: Path, repeat: int | None = None,
             live_apply: bool = False, fixture_dir: Path | None = None) -> list[Path]:
    count = planned_runs(task, target=target, repeat=repeat, live_apply=live_apply)
    return [run_once(task, n, target=target, model_kind=model_kind, set_dir=set_dir, fixture_dir=fixture_dir)
            for n in range(1, count + 1)]


def _arm(transport: Any, on: bool) -> None:
    """One-shot fake-server faults are for the agent, not for the harness's own calls."""
    if hasattr(transport, "armed"):
        transport.armed = on


def run_once(task: Task, n: int, *, target: str, model_kind: str, set_dir: Path, fixture_dir: Path | None = None) -> Path:
    path = set_dir / task.id / f"{n}.jsonl"
    settings = get_settings(task.business)
    mode = "apply" if task.mode == "apply" else "plan"
    trace = Trace(None, Redactor(settings.secrets()))
    try:
        fixture = fixtures.load(task.business, fixture_dir) if target == "fake" or mode == "apply" else None
        transport = make_transport(target, settings, fixture_dir, task.faults, list(task.extra_files))
        _arm(transport, False)
        rt = build(task.business, target, mode, None, transport=transport, settings=settings, trace=trace,
                   live_allowlist=task.live_allowlist)
        manifest = build_manifest(task, n, target, model_kind, rt, fixture)
    except WritesNotAllowed:
        raise
    except Exception as err:  # still leave a run file, so the failure is scored instead of vanishing
        trace.attach(path, stub_manifest(task, n, target, model_kind))
        trace.write("result", passes=[], error=f"set-up failed: {type(err).__name__}: {err}", state_before=None,
                    state_after=None, resolved_ids={}, fake_write_log=None)
        return path
    trace.attach(path, manifest)
    _run_passes(task, n, rt, transport, fixture, path, model_kind, settings)
    return path


def _run_passes(task: Task, n: int, rt: Any, transport: Any, fixture: Any, path: Path, model_kind: str, settings: Any) -> None:
    live_write = rt.target == "live" and rt.guard.can_write
    ids = scope_ids(task, rt.guard.allowlist)
    journal = snapshot.WriteJournal(path.with_name(f"writes-{n}.json") if live_write else None)
    snap, passes, error, state_before = None, [], None, None
    try:
        state_before = capture_state(rt, ids)
        if live_write:
            preflight.require_ok(rt, fixture)
            snap = snapshot.take(rt.admin_mcp, rt.guard.allowlist)
            snapshot.save(snap, path.with_name(f"snapshot-{n}.json"))
        for p in range(task.passes):
            if p:
                rt = build(task.business, rt.target, "apply" if rt.guard.can_write else "plan", None,
                           transport=transport, settings=settings, trace=rt.trace, live_allowlist=task.live_allowlist)
            rt.guard.journal = journal.add
            rt.trace.write("pass_start", index=p)
            _arm(transport, True)
            try:
                res = run_agent(task.question, rt.ctx, make_model(model_kind, settings), rt.budget, rt.trace)
            finally:
                _arm(transport, False)
            passes.append({"index": p, "answer": res.answer, "model_text": res.model_text, "cited_ids": res.cited_ids,
                           "unverified_ids": res.unverified_ids, "records": res.records, "writes": rt.guard.writes,
                           "ledger": rt.budget.ledger(), "stop": res.stop, "aborted": res.aborted})
    except Exception as err:  # the run file must still be completed and restored
        error = f"{type(err).__name__}: {err}"
        rt.trace.write("run_error", error=error)
    finally:
        try:
            _write_result(rt, ids, passes, error, state_before, transport)
        finally:
            if snap is not None:
                _restore(rt, snap, journal, ids)


def _write_result(rt: Any, ids: list[str], passes: list[dict[str, Any]], error: str | None,
                  state_before: Any, transport: Any) -> None:
    state_after, resolved = None, {}
    try:
        state_after = capture_state(rt, ids)
        resolved = resolve_ids(rt, passes[-1]["cited_ids"] if passes else [])
    except Exception as err:
        error = error or f"state capture failed: {type(err).__name__}: {err}"
    rt.trace.write("result", passes=passes, error=error, state_before=state_before, state_after=state_after,
                   resolved_ids=resolved, fake_write_log=getattr(transport, "write_log", None))


def _restore(rt: Any, snap: dict[str, Any], journal: snapshot.WriteJournal, ids: list[str]) -> None:
    try:
        snapshot.restore(rt.admin_mcp, snap, rt.trace, allowlist=KEYSTONE_INCOMING_ALLOWLIST, writes=journal.entries,
                         me=rt.session.me().get("id"))
    finally:
        try:
            rt.trace.write("post_restore", state=capture_state(rt, ids))
        except Exception as err:
            rt.trace.write("post_restore", error=f"{type(err).__name__}: {err}")
