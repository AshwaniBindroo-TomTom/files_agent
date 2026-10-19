"""Tool discovery at start-up (T1.3). The catalogue changes often, so never hard-code it."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from agent.config import (ASSIGNEES_TOOL, ESCALATION_TOOL, EXPOSED_READ_TOOLS, OPTIONAL_TOOLS, RAISE_TOOL,
                          REQUIRED_TOOLS, WRITE_TOOLS)


@dataclass(frozen=True)
class Catalog:
    tools: dict[str, dict[str, Any]]

    @classmethod
    def from_tools(cls, tools: list[dict[str, Any]]) -> "Catalog":
        return cls({t["name"]: t for t in tools})

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.tools)

    def required_args(self, name: str) -> list[str]:
        return list((self.tools.get(name, {}).get("inputSchema") or {}).get("required") or [])

    def is_read_only(self, name: str) -> bool:
        return bool((self.tools.get(name, {}).get("annotations") or {}).get("readOnlyHint"))

    def can_list(self, entity: str) -> bool:
        """An entity is inside this seat's boundary only if its .list tool exists."""
        return f"{entity}.list" in self.tools

    def hash(self) -> str:
        """Stable fingerprint of names + input schemas, used in run manifests and pre-flight."""
        canon = sorted((n, json.dumps(t.get("inputSchema") or {}, sort_keys=True)) for n, t in self.tools.items())
        return hashlib.sha256(json.dumps(canon).encode("utf-8")).hexdigest()[:16]

    def text_hash(self) -> str:
        """STRIDE T10: fingerprint of the agent's own tools with their descriptions and annotations (hash() covers
        names and input schemas only), so pre-flight also sees a text-only or read-only-mark change to them."""
        names = sorted({*REQUIRED_TOOLS, *OPTIONAL_TOOLS, *EXPOSED_READ_TOOLS, *WRITE_TOOLS})
        canon = [(n, json.dumps({k: (self.tools.get(n) or {}).get(k) for k in ("description", "inputSchema", "annotations")},
                                sort_keys=True)) for n in names]
        return hashlib.sha256(json.dumps(canon).encode("utf-8")).hexdigest()[:16]

    def check_required(self) -> list[str]:
        """Problems with the tools the agent depends on (empty list = all good). A missing optional tool is not a
        problem (see missing_optional), but one that is there and now wants other arguments still is."""
        problems = []
        for name, args in {**REQUIRED_TOOLS, **OPTIONAL_TOOLS}.items():
            if name not in self.tools:
                if name in REQUIRED_TOOLS:
                    problems.append(f"missing tool {name}")
            elif set(self.required_args(name)) - set(args):
                problems.append(f"{name} now requires {sorted(set(self.required_args(name)) - set(args))}")
        return problems

    def escalation_tool(self) -> str | None:
        """How this seat hands a file to a person: create while the platform still offers it (both were listed until
        15 Oct), else raise with its assignee list (create's replacement), else None."""
        if ESCALATION_TOOL in self.tools:
            return ESCALATION_TOOL
        return RAISE_TOOL if RAISE_TOOL in self.tools and ASSIGNEES_TOOL in self.tools else None

    def missing_optional(self) -> list[str]:
        """Optional tools the platform does not offer this seat: the agent runs without what they do."""
        return sorted(name for name in OPTIONAL_TOOLS if name not in self.tools)

    def report(self) -> str:
        problems = self.check_required()
        head = f"{len(self.tools)} tools; hash {self.hash()}"
        tool = self.escalation_tool()
        tail = f"; escalates with {tool}" if tool else "; NO escalation tool (cases are recorded, not escalated)"
        return head + ("; all required tools present" if not problems else "; PROBLEMS: " + "; ".join(problems)) + tail
