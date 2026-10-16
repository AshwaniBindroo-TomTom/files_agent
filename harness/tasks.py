"""Task files (T4.1). One TOML file per task in harness/tasks/. The expectations are TEAM-OWNED."""
from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from agent.config import REPO_ROOT
from harness.fake_server import extra_file_id

TASK_DIR = REPO_ROOT / "harness" / "tasks"
TASK_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
KNOWN_EXPECT = {
    "writes", "final_folders", "archived", "unchanged", "escalations_new", "last_pass_escalations", "escalation_for",
    "answer_must_mention", "answer_must_not_mention", "answer_must_cite", "answer_must_not_cite",
    "cited_ids_must_resolve", "planned_folders", "planned_not_filed", "aborted",
    "records_must_include", "run_must_not_contain", "no_events",
}


@dataclass(frozen=True)
class Task:
    id: str
    question: str
    business: str = "keystone"
    mode: str = "read"            # read = plan-only; apply = writes allowed
    repeat: int = 5
    passes: int = 1               # ask the same question N times in the same environment (idempotency)
    faults: tuple[str, ...] = ()
    extra_files: tuple[dict[str, Any], ...] = ()
    live_write: bool = False      # may this apply task write on the LIVE platform? (only one task)
    live_allowlist: bool = False  # offline: cap the write allow-list to the live 9 (live always does), to rehearse the live run
    needs_tools: tuple[str, ...] = ()  # skipped, not failed, when the platform lacks one: it can't pass without it
    expect: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(**{**data, "faults": tuple(data.get("faults", ())), "extra_files": tuple(data.get("extra_files", ())),
                      "needs_tools": tuple(data.get("needs_tools", ()))})


TASK_FIELDS = {f.name for f in fields(Task)}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:  # STRIDE D4: say which file is broken
        raise ValueError(f"{path.name}: not valid TOML: {err}") from err


def _id_problem(path: Path, data: dict[str, Any]) -> str | None:
    """STRIDE T14: the id names the run folder, so it must be the file's own name and path-safe."""
    task_id = data.get("id")
    if isinstance(task_id, str) and TASK_ID_RE.fullmatch(task_id) and task_id == path.stem:
        return None
    return f"id must equal the file name {path.stem!r} and use only letters, digits, '_' and '-'; found {task_id!r}"


def _type_problems(data: dict[str, Any]) -> list[str]:
    """STRIDE T14: a quoted bool is truthy and passes = 0 runs nothing, so wrong types are refused, not coerced."""
    problems = [f"{k} must be a string" for k in ("question", "business", "notes", "mode") if k in data and not isinstance(data[k], str)]
    if isinstance(data.get("question"), str) and not data["question"].strip():
        problems.append("question must not be empty")
    problems += [f"{k} must be true or false" for k in ("live_write", "live_allowlist") if k in data and type(data[k]) is not bool]
    problems += [f"{k} must be a whole number of at least 1" for k in ("repeat", "passes")
                 if k in data and (type(data[k]) is not int or data[k] < 1)]
    faults, extra = data.get("faults", []), data.get("extra_files", [])
    if not isinstance(faults, list) or not all(isinstance(f, str) for f in faults):
        problems.append("faults must be a list of strings")
    if not isinstance(extra, list) or not all(isinstance(f, dict) for f in extra):
        problems.append("extra_files must be a list of tables")
    needs = data.get("needs_tools", [])
    if not isinstance(needs, list) or not all(isinstance(t, str) and t for t in needs):
        problems.append("needs_tools must be a list of tool names")
    if not isinstance(data.get("expect", {}), dict):
        problems.append("expect must be a table")
    return problems


def _extra_file_problems(extra: Any) -> list[str]:
    """Each planted file needs a filename and its own id: two with one id would make one row (fake_server refuses)."""
    if not isinstance(extra, list) or not all(isinstance(f, dict) for f in extra):
        return []  # _type_problems reports it
    problems: list[str] = []
    seen: dict[str, str] = {}
    for n, spec in enumerate(extra, 1):
        name = spec.get("filename")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"extra_files #{n} needs a filename")
            continue
        if "id" in spec and (not isinstance(spec["id"], str) or not spec["id"].strip()):
            problems.append(f"extra_files {name!r}: id must be a non-empty string")
            continue
        file_id = extra_file_id(spec)
        if file_id in seen:
            problems.append(f"extra_files {name!r} has the same id {file_id} as {seen[file_id]!r}; "
                            "give one of them its own `id`")
        seen.setdefault(file_id, name)
    return problems


def load_task(path: Path) -> Task:
    data = _read_toml(path)
    unknown_keys = set(data) - TASK_FIELDS
    if unknown_keys:  # STRIDE D4: a typo such as `repeats` names the file instead of crashing in Task()
        raise ValueError(f"{path.name}: unknown key(s) {sorted(unknown_keys)}; allowed: {sorted(TASK_FIELDS)}")
    missing = sorted({"id", "question"} - set(data))
    if missing:
        raise ValueError(f"{path.name}: missing {missing}")
    problems = [p for p in [_id_problem(path, data), *_type_problems(data),
                            *_extra_file_problems(data.get("extra_files", []))] if p]
    if problems:
        raise ValueError(f"{path.name}: " + "; ".join(problems))
    unknown = set(data.get("expect", {})) - KNOWN_EXPECT
    if unknown:
        raise ValueError(f"{path.name}: unknown expectation(s) {sorted(unknown)}")
    if data.get("mode", "read") not in ("read", "apply"):
        raise ValueError(f"{path.name}: mode must be 'read' or 'apply'")
    return Task.from_dict(data)


def _refuse_duplicates(paths: list[Path], tasks: list[Task]) -> None:
    """STRIDE T14: a second file with the same id (ignoring case, as Windows folders do) must not replace a task."""
    seen: dict[str, Path] = {}
    for path, task in zip(paths, tasks):
        key = task.id.casefold()
        if key in seen:
            raise ValueError(f"task id {task.id!r} in {path.name} is already used by {seen[key].name}")
        seen[key] = path


def load_all(folder: Path = TASK_DIR) -> dict[str, Task]:
    paths = [p for p in sorted(folder.glob("*.toml")) if p.stem != "routes"]
    tasks = [load_task(p) for p in paths]
    _refuse_duplicates(paths, tasks)
    live = [t.id for t in tasks if t.live_write]
    if len(live) > 1:
        raise ValueError(f"only one task may set live_write = true (one live write run is planned), found {live}")
    uncapped = [t.id for t in tasks if t.live_write and not t.live_allowlist]
    if uncapped:
        raise ValueError(f"{uncapped} set live_write without live_allowlist, so their offline rehearsal would not match live")
    return {t.id: t for t in tasks}
