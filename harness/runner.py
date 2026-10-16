"""Runner (T1.8 / T4.2). Every run is written to disk before anything is scored.

One run = one repeat of one task, in a fresh environment (a fresh fake server,
or the live platform). A task with passes=2 asks its question twice in the same
environment (idempotency). The run file holds: the manifest (first line), every
event, and a final `result` event with the scoped database state before and after.

Guarantees:
- A run whose set-up fails still gets a run file (stub manifest + failed result).
- Live write runs are wrapped in pre-flight, snapshot, a write journal and a restore
  that runs even if collecting the after-state fails.
- A live write task runs once: a second run is refused before it logs in, so it can
  never overwrite the first run's evidence, snapshot or journal. An earlier attempt that
  provably sent nothing (set-up or pre-flight failed, or it stopped before its first write)
  does not count: its files are moved to an attempt-<time>/ folder and the run goes ahead.
- A pass that fails part-way still leaves its decision records and writes in the run file.
- Tasks that depend on the fake server (faults, extra files) never run live.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agent import snapshot
from agent.config import KEYSTONE_INCOMING_ALLOWLIST, RUNS_DIR, WRITE_TOOLS, get_settings
from agent.loop import run_agent
from agent.mcp_client import McpError
from agent.redact import Redactor
from agent.runtime import WritesNotAllowed, build, check_write_permission, make_model, make_transport
from agent.safe_reads import list_all
from agent.trace import Trace, read_trace
from harness import fixtures, preflight
from harness.manifest import build_manifest, stub_manifest
from harness.tasks import Task

STATE_FIELDS = (*snapshot.WRITABLE_FIELDS, "updated_at", "updated_by")
# AgentSession.list: the record trail can cite the run's own session (another group's STRIDE R3 fix).
# STRIDE T5: DriveAccessLog rows and FileAttachment rows are shown to the model too, with user and company ids.
ID_TOOLS = ("Item.list", "DriveFolder.list", "Party.list", "AgentEscalation.list", "AgentSession.list",
            "DriveAccessLog.list", "FileAttachment.list")
PERSON_FIELDS = ("created_by", "updated_by", "company_id")  # ids of people and companies on those rows
ATTEMPT_FILE = re.compile(r"^(?:(?P<run>\d+)\.jsonl|(?:snapshot|writes)-(?P<undo>\d+)(?:\.meta)?\.json)$")


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
            known = _known_ids(rt)
        out[rid] = rid in known
    return out


def _known_ids(rt: Any) -> set[str]:
    """Every id the platform shows on the ID_TOOLS rows, plus the seat's own user and company ids."""
    me = rt.session.me()
    known = {str(me[k]) for k in ("id", "company_id") if me.get(k)}
    for tool in (t for t in ID_TOOLS if t in rt.catalog.names):
        try:
            rows = list_all(rt.admin_mcp, tool)
        except McpError as err:
            rt.trace.write("resolve_ids_warning", tool=tool, error=str(err))
            continue
        known.update(str(r[k]) for r in rows for k in ("id", *PERSON_FIELDS) if r.get(k))
    return known


def planned_runs(task: Task, *, target: str, repeat: int | None = None, live_apply: bool = False,
                 set_dir: Path | None = None) -> int:
    """How many run files this task will produce. Raises RunRefused / WritesNotAllowed before anything is sent."""
    if target == "live" and (task.faults or task.extra_files):
        raise RunRefused(f"{task.id} uses fake-server faults or extra files, so it runs offline only")
    live_write = target == "live" and task.mode == "apply"
    if live_write and not task.live_write:
        raise RunRefused(f"{task.id} is not marked live_write = true, so it never writes on the live platform "
                         "(only TI2L is: its escalations are permanent, and one live write run is planned)")
    if live_write and not live_apply:
        raise RunRefused(f"{task.id} writes; on the live platform it needs --live-apply and AS_ALLOW_WRITES=1")
    if live_write and set_dir is not None:
        refuse_live_rerun(task, set_dir)
    check_write_permission(target, "apply" if task.mode == "apply" else "plan", get_settings(task.business))
    if live_write and set_dir is not None:
        set_aside_attempts(task, set_dir)
    return 1 if live_write else (repeat or task.repeat)


def refuse_live_rerun(task: Task, set_dir: Path) -> None:
    """STRIDE R1: live write tasks run once, so never start one after a live attempt that may have sent a write."""
    here = (set_dir / task.id).resolve()
    folders = {here, *(d.resolve() for d in RUNS_DIR.glob(f"*/{task.id}") if d.is_dir())}
    found = sorted(p for d in folders for n, files in _attempts(d).items() if _may_have_written(d, n, files)
                   for p in files)
    if found:
        raise RunRefused(f"{task.id} already ran live and may have sent writes ({found[0]}), and live write tasks run "
                         "once; nothing was overwritten. To undo that run use `python -m harness restore`; move its "
                         "folder out of runs/ only if a second live run has been approved")


def set_aside_attempts(task: Task, set_dir: Path) -> None:
    """Move the files of earlier attempts that sent nothing out of the new run's way (scoring skips sub-folders)."""
    here = set_dir / task.id
    files = sorted(p for paths in _attempts(here).values() for p in paths)
    if not files:
        return
    dest = here / f"attempt-{datetime.now():%Y%m%d-%H%M%S-%f}"
    dest.mkdir(parents=True)
    for path in files:
        path.rename(dest / path.name)
    print(f"{task.id}: an earlier attempt here sent no write; moved {', '.join(p.name for p in files)} to {dest}")


def _attempts(folder: Path) -> dict[str, list[Path]]:
    """{attempt number: its run file, snapshot, snapshot meta and journal} found directly in `folder`."""
    out: dict[str, list[Path]] = {}
    for path in (folder.iterdir() if folder.is_dir() else []):
        match = ATTEMPT_FILE.match(path.name)
        if match and path.is_file():
            out.setdefault(match.group("run") or match.group("undo"), []).append(path)
    return out


def _may_have_written(folder: Path, n: str, files: list[Path]) -> bool:
    """Only a finished live run file with no write event, and an empty (or no) journal, proves nothing was sent."""
    run, journal = folder / f"{n}.jsonl", folder / f"writes-{n}.json"
    if files == [run] and _target_of(run) == "fake":  # an offline run: it never writes to the live platform
        return False
    events = _events(run)
    if events is None or not any(e.get("kind") == "result" for e in events):
        return True  # unreadable, missing, or stopped mid-pass: a write may have landed
    if any(e.get("kind") == "write_blocked" or (e.get("kind") == "mcp_call" and e.get("tool") in WRITE_TOOLS)
           for e in events):
        return True
    return journal.exists() and _json(journal) != []


def _target_of(run: Path) -> str | None:
    try:
        with run.open(encoding="utf-8") as fh:
            return json.loads(fh.readline()).get("target")
    except (OSError, ValueError, AttributeError):
        return None


def _events(run: Path) -> list[dict[str, Any]] | None:
    try:
        events = read_trace(run)
    except (OSError, ValueError):
        return None
    return events if all(isinstance(e, dict) for e in events) else None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_task(task: Task, *, target: str, model_kind: str, set_dir: Path, repeat: int | None = None,
             live_apply: bool = False, fixture_dir: Path | None = None) -> list[Path]:
    count = planned_runs(task, target=target, repeat=repeat, live_apply=live_apply, set_dir=set_dir)
    return [run_once(task, n, target=target, model_kind=model_kind, set_dir=set_dir, fixture_dir=fixture_dir)
            for n in range(1, count + 1)]


def run_id(path: Path) -> str:
    """STRIDE R3: <set>/<task>/<n>, the run file a permanent session row belongs to (it is in the session title)."""
    return f"{path.parent.parent.name}/{path.parent.name}/{path.stem}"


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
        rt.ctx.run_id = run_id(path)
        missing = [tool for tool in task.needs_tools if tool not in rt.catalog.names]
        if missing:  # the review of run c2224f4d (15 Oct): skip only the task that needs it, not the whole pre-flight
            raise RunRefused(f"{task.id} needs {', '.join(missing)}, which the platform no longer offers this seat; "
                             "nothing was sent")
        manifest = build_manifest(task, n, target, model_kind, rt, fixture)
    except (WritesNotAllowed, RunRefused):
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
    snap, passes, error, state_before, in_pass = None, [], None, None, None
    try:
        state_before = capture_state(rt, ids)
        if live_write:
            preflight.require_ok(rt, fixture)
            snap = snapshot.take(rt.admin_mcp, rt.guard.allowlist)
            _save_undo_files(rt, snap, journal, path.with_name(f"snapshot-{n}.json"))
        for p in range(task.passes):
            if p:
                rt = build(task.business, rt.target, "apply" if rt.guard.can_write else "plan", None,
                           transport=transport, settings=settings, trace=rt.trace, live_allowlist=task.live_allowlist)
                rt.ctx.run_id = run_id(path)
            rt.guard.journal = journal.add
            rt.trace.write("pass_start", index=p)
            _arm(transport, True)
            in_pass = p
            try:
                res = run_agent(task.question, rt.ctx, make_model(model_kind, settings), rt.budget, rt.trace)
            finally:
                _arm(transport, False)
            in_pass = None
            passes.append({"index": p, "answer": res.answer, "model_text": res.model_text, "cited_ids": res.cited_ids,
                           "unverified_ids": res.unverified_ids, "records": res.records, "writes": rt.guard.writes,
                           "ledger": rt.budget.ledger(), "stop": res.stop, "aborted": res.aborted})
    except Exception as err:  # the run file must still be completed and restored
        error = f"{type(err).__name__}: {err}"
        _record_failure(rt, error, in_pass, passes)
    except BaseException as err:  # STRIDE R5: a Ctrl-C is recorded too; the result and restore below still run
        error = f"interrupted: {type(err).__name__}"
        _record_failure(rt, error, in_pass, passes)
        raise
    finally:
        try:
            _write_result(rt, ids, passes, error, state_before, transport)
        finally:
            if snap is not None:
                _restore(rt, snap, journal, ids)


def _save_undo_files(rt: Any, snap: dict[str, Any], journal: snapshot.WriteJournal, snap_path: Path) -> None:
    """The snapshot, where it came from, and an empty journal, so even a run that never writes leaves one."""
    snapshot.save(snap, snap_path)
    # STRIDE S3: restore refuses to replay this snapshot against any other target.
    snapshot.save({"target": rt.target, "business": rt.settings.business, "base_url": rt.settings.base_url,
                   "user_id": rt.session.me().get("id")}, snap_path.with_name(f"{snap_path.stem}.meta.json"))
    if journal.path is not None:  # STRIDE T6: restore refuses to run without a journal, so always leave one
        snapshot.save(journal.entries, journal.path)


def _record_failure(rt: Any, error: str, in_pass: int | None, passes: list[dict[str, Any]]) -> None:
    rt.trace.write("run_error", error=error)
    partial = _partial_pass(rt, in_pass, error) if in_pass is not None else None
    if partial:
        passes.append(partial)


def _partial_pass(rt: Any, index: int, error: str) -> dict[str, Any] | None:
    """STRIDE R5: what a failed pass already did (records, writes, cost), or None if it had not started."""
    records, writes = rt.ctx.records.to_list(), rt.guard.writes
    if not (records or writes or rt.budget.turns):
        return None
    return {"index": index, "answer": "", "model_text": "", "cited_ids": [], "unverified_ids": [], "records": records,
            "writes": writes, "ledger": rt.budget.ledger(), "stop": error, "aborted": "error", "partial": True}


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
