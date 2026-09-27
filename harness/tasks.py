"""Task files (T4.1). One TOML file per task in harness/tasks/. The expectations are TEAM-OWNED."""
from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.config import REPO_ROOT

TASK_DIR = REPO_ROOT / "harness" / "tasks"
KNOWN_EXPECT = {
    "writes", "final_folders", "archived", "unchanged", "escalations_new", "last_pass_escalations", "escalation_for",
    "answer_must_mention", "answer_must_not_mention", "answer_must_cite", "answer_must_not_cite",
    "cited_ids_must_resolve", "planned_folders", "planned_not_filed", "aborted",
    "records_must_include", "run_must_not_contain",
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
    expect: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(**{**data, "faults": tuple(data.get("faults", ())), "extra_files": tuple(data.get("extra_files", ()))})


def load_task(path: Path) -> Task:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(data.get("expect", {})) - KNOWN_EXPECT
    if unknown:
        raise ValueError(f"{path.name}: unknown expectation(s) {sorted(unknown)}")
    if data.get("mode", "read") not in ("read", "apply"):
        raise ValueError(f"{path.name}: mode must be 'read' or 'apply'")
    return Task.from_dict(data)


def load_all(folder: Path = TASK_DIR) -> dict[str, Task]:
    tasks = [load_task(p) for p in sorted(folder.glob("*.toml")) if p.stem != "routes"]
    live = [t.id for t in tasks if t.live_write]
    if len(live) > 1:
        raise ValueError(f"only one task may set live_write = true (one live write run is planned), found {live}")
    uncapped = [t.id for t in tasks if t.live_write and not t.live_allowlist]
    if uncapped:
        raise ValueError(f"{uncapped} set live_write without live_allowlist, so their offline rehearsal would not match live")
    return {t.id: t for t in tasks}
