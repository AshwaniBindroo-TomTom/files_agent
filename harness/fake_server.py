"""Offline fake of the AgentSwitch API (T1.7), replaying a captured fixture.

Same interface as agent.http.HttpTransport: request(method, path, token, body).
Writes change only in-memory state. Faults (T4.5) are strings:

  http401_once                          next /api/mcp call answers 401 once
  error_in_200:<Tool.name>              next call of that tool returns a JSON-RPC error
  moved_row:<file_id>:<folder_id>       on the first FileAttachment.update, "another team" moves that file first
  foreign_change:<file_id>              on the first FileAttachment.update, "another team" retags that file
  clobber_after_write:<file_id>:<folder_id>  right after OUR update of that file, "another team" moves it
                                        to <folder_id>, so our confirming read no longer sees our change
  drift_updated_at:<file_id>            at start, the file's updated_at differs from the fixture
  missing_tool:<Tool.name>              at start, the tool is gone from tools/list
  swap_archived:<id1>:<id2>             at start, swap is_archived between two files
  planted_description:<file_id>:<text>  at start, replace a file's description
  raise_only[:<party_id>,...]           at start, AgentEscalation.create is gone (as on Keystone since 15 Oct),
                                        so escalations go through escalations.raise; escalations.assignees
                                        lists these parties (none given = no one, as on Keystone)

The first five are one-shot faults. They fire only while `armed` is True. The harness
runner disarms them during its own set-up and state capture and arms them for the
agent's pass, so the agent is the one that meets them. A server you build yourself
(e.g. in a test) starts armed.

Extra files (a task's `extra_files`) are added as FileAttachment rows. Each one's id comes from
its filename (see extra_file_id) unless the spec gives an `id`, so two extras can share a name.
An id that is already a row (a fixture row or an earlier extra) is refused, never replaced.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import ASSIGNEES_TOOL, ESCALATION_TOOL, RAISE_TOOL
from harness import fixtures

CONTROL_ARGS = {"limit", "offset", "sort_by", "sort_order", "search"}
DEFAULT_READONLY = ["is_trashed", "trashed_at", "trashed_by", "restored_at", "is_purged",
                    "purged_at", "purged_by", "retention_expires_at", "retention_policy_id"]
FOREIGN_USER = "00000000-0000-0000-0000-0000000f0e19"


class RpcError(Exception):
    def __init__(self, code: int, message: str, data_code: str) -> None:
        super().__init__(message)
        self.code, self.message, self.data_code = code, message, data_code


def extra_file_id(spec: dict[str, Any]) -> str:
    """The row id of an extra file: its own `id`, else one derived from its filename (stable across runs)."""
    return str(spec.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, "extra-file:" + spec["filename"]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "")


class FakeServer:
    is_fake = True  # STRIDE E1: agent.runtime.build refuses a fake labelled "live", or anything else labelled "fake"

    def __init__(self, fixture: dict[str, Any], faults: tuple[str, ...] = (), extra_files: list[dict[str, Any]] | None = None) -> None:
        self.fixture = copy.deepcopy(fixture)
        self.me = self.fixture["me"]
        self.tools = list(self.fixture["tools"])
        self.tables = {n: {r["id"]: dict(r) for r in rows} for n, rows in self.fixture["tables"].items()}
        self.faults = [f for f in faults if f]
        self.fired: set[str] = set()
        self.armed = True  # one-shot faults fire only while armed (see module docstring)
        self.write_log: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.assignees: list[str] = []  # party ids escalations.assignees lists (the raise_only fault sets them)
        for spec in extra_files or []:
            self._add_file(spec)
        self._startup_faults()

    @classmethod
    def from_fixture(cls, business: str, fixture_dir: Path | None, faults: tuple[str, ...] = (),
                     extra_files: list[dict[str, Any]] | None = None) -> "FakeServer":
        return cls(fixtures.load(business, fixture_dir), faults, extra_files)

    # ---------------------------------------------------------------- setup
    def _folder_id(self, name: str) -> str:
        return next(f["id"] for f in self.tables["DriveFolder"].values() if f["name"].lower() == name.lower())

    def _add_file(self, spec: dict[str, Any]) -> None:
        file_id = extra_file_id(spec)
        if file_id in self.tables["FileAttachment"]:  # never silently replace a fixture row or an earlier extra
            raise ValueError(f"extra file {spec['filename']!r}: id {file_id} is already used; give it its own `id`")
        folder = spec.get("folder", "Incoming")  # "" = in no folder
        row = {"id": file_id, "filename": spec["filename"], "folder_id": self._folder_id(folder) if folder else None,
               "entity_type": spec.get("entity_type"), "entity_id": spec.get("entity_id"), "description": spec.get("description"),
               "tags": spec.get("tags", "untriaged"), "party_id": spec.get("party_id"), "from_email": spec.get("from_email"),
               "content_hash": spec.get("content_hash"), "size_bytes": spec.get("size_bytes", 1000), "is_archived": 0,
               "created_at": _now(), "updated_at": _now(), "updated_by": None, "_permissions": {"write": True, "delete": False},
               "_readonly_fields": list(DEFAULT_READONLY)}
        self.tables["FileAttachment"][file_id] = row

    def _startup_faults(self) -> None:
        files = self.tables["FileAttachment"]
        for fault in self.faults:
            kind, _, rest = fault.partition(":")
            if kind == "drift_updated_at":
                files[rest]["updated_at"] = _now()
            elif kind == "missing_tool":
                self.tools = [t for t in self.tools if t["name"] != rest]
            elif kind == "raise_only":
                self.tools = [t for t in self.tools if t["name"] != ESCALATION_TOOL]
                self.assignees = [p for p in rest.split(",") if p]
            elif kind == "swap_archived":
                a, b = rest.split(":")
                files[a]["is_archived"], files[b]["is_archived"] = files[b].get("is_archived"), files[a].get("is_archived")
            elif kind == "planted_description":
                file_id, _, text = rest.partition(":")
                files[file_id]["description"] = text

    def _once(self, fault: str) -> bool:
        if self.armed and fault in self.faults and fault not in self.fired:
            self.fired.add(fault)
            return True
        return False

    # ---------------------------------------------------------------- transport
    def request(self, method: str, path: str, token: str | None = None, body: Any = None,
                idempotent: bool = True) -> tuple[int, Any]:  # idempotent: same signature as HttpTransport
        if path == "/api/auth/login" and method == "POST":
            return 200, {"token": "fake-token-" + uuid.uuid4().hex[:12]}
        if not token or not token.startswith("fake-token-"):
            return 401, {"detail": "Authentication required"}
        if path == "/api/mcp" and method == "POST":
            if self._once("http401_once"):
                return 401, {"detail": "Authentication required"}
            return 200, self._rpc(body or {})
        if method == "GET" and path == "/api/auth/me":
            return 200, self.me
        if method == "GET" and path in self.fixture.get("rest", {}):
            return 200, self.fixture["rest"][path]
        return 404, {"detail": "Not Found"}

    def _rpc(self, body: dict[str, Any]) -> dict[str, Any]:
        rid, method, params = body.get("id"), body.get("method"), body.get("params") or {}
        try:
            if method == "initialize":
                result: Any = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-agentswitch"}}
            elif method == "tools/list":
                result = {"tools": self.tools}
            elif method == "tools/call":
                payload = self._call(params.get("name", ""), params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(payload, default=str)}], "structuredContent": payload, "isError": False}
            else:
                raise RpcError(-32601, "Method not found", "method_not_found")
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except RpcError as err:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": err.code, "message": err.message, "data": {"code": err.data_code}}}

    # ---------------------------------------------------------------- tools
    def _tool(self, name: str) -> dict[str, Any]:
        tool = next((t for t in self.tools if t["name"] == name), None)
        if tool is None:
            raise RpcError(-32602, "This tool is not available to your seat; it is not in your tools/list.", "tool_not_available")
        return tool

    def _validate(self, tool: dict[str, Any], args: dict[str, Any]) -> None:
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        unknown = [k for k in args if k not in props]
        missing = [k for k in schema.get("required") or [] if k not in args]
        if (unknown and schema.get("additionalProperties") is False) or missing:
            raise RpcError(-32602, "Invalid tool arguments.", "invalid_arguments")

    def _call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append(name)
        tool = self._tool(name)
        self._validate(tool, args)
        if self._once(f"error_in_200:{name}"):
            raise RpcError(-32603, "Injected failure (fake server)", "agent_error")
        if name == ASSIGNEES_TOOL:
            options = [{"party_id": pid, "display": (self.tables.get("Party", {}).get(pid) or {}).get("name")}
                       for pid in self.assignees]
            return {"status": "ok", "result": {"ok": True, "options": options,
                                               **({} if options else {"reason_code": "assignees.noLinkedSignIns"})}}
        if name == RAISE_TOOL:
            return self._raise(args)
        if name == "endpoint.people_directory":
            return {"result": {"items": self.fixture.get("people_directory", [])}, "status": "ok"}
        if name == "tools.search":
            words = str(args.get("query", "")).lower().split()
            hits = [t for t in self.tools if all(w in (t["name"] + " " + (t.get("description") or "")).lower() for w in words)]
            return {"results": [{"name": t["name"], "description": t.get("description")} for t in hits[: int(args.get("limit", 10))]], "status": "ok"}
        if name == "tools.describe":
            names = list(args.get("names") or [])
            found = [t for t in self.tools if t["name"] in names]
            return {"results": found, "not_available": [n for n in names if n not in {t["name"] for t in found}], "status": "ok"}
        entity, _, op = name.rpartition(".")
        table = self.tables.setdefault(entity, {})
        if op == "list":
            return self._list(table, args)
        if op == "get":
            return self._get(table, args["id"])
        if op == "update" and entity == "FileAttachment":
            return self._update_file(args)
        if op == "create":
            return self._create(entity, table, args)
        raise RpcError(-32602, f"{name} is not emulated by the fake server", "not_emulated")

    def _list(self, table: dict[str, dict[str, Any]], args: dict[str, Any]) -> dict[str, Any]:
        rows = list(table.values())
        for key, value in args.items():
            if key not in CONTROL_ARGS:
                rows = [r for r in rows if _loose_eq(r.get(key), value)]
        if args.get("search"):
            needle = str(args["search"]).lower()
            rows = [r for r in rows if any(needle in str(r.get(f) or "").lower() for f in ("filename", "_display", "code", "name", "subject"))]
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        limit, offset = min(int(args.get("limit", 20)), 1000), int(args.get("offset", 0))
        return {"data": copy.deepcopy(rows[offset: offset + limit]), "total": len(rows), "limit": limit, "offset": offset}

    def _get(self, table: dict[str, dict[str, Any]], row_id: str) -> dict[str, Any]:
        if row_id not in table:
            raise RpcError(-32602, "Record not found", "not_found")
        return copy.deepcopy(table[row_id])

    def _update_file(self, args: dict[str, Any]) -> dict[str, Any]:
        files = self.tables["FileAttachment"]
        self._fire_foreign_faults(files)
        row = files.get(args["id"])
        if row is None:
            raise RpcError(-32602, "Record not found", "not_found")
        changes = {k: v for k, v in args.items() if k != "id"}
        blocked = set(changes) & set(row.get("_readonly_fields") or [])
        if blocked or not (row.get("_permissions") or {}).get("write"):
            raise RpcError(-32602, f"Read-only or not writable: {sorted(blocked)}", "invalid_arguments")
        row.update(changes, updated_at=_now(), updated_by=self.me["id"])
        self.write_log.append({"tool": "FileAttachment.update", "id": args["id"], "changes": changes, "by": self.me["id"]})
        reply = copy.deepcopy(row)
        self._fire_clobber(files, args["id"])
        return reply

    def _fire_clobber(self, files: dict[str, dict[str, Any]], file_id: str) -> None:
        """clobber_after_write: "another team" overwrites our change right after we wrote it (before our confirm read)."""
        for fault in list(self.faults):
            kind, _, rest = fault.partition(":")
            target, _, folder_id = rest.partition(":")
            if kind == "clobber_after_write" and target == file_id and self._once(fault):
                files[file_id].update(folder_id=folder_id, updated_at=_now(), updated_by=FOREIGN_USER)

    def _fire_foreign_faults(self, files: dict[str, dict[str, Any]]) -> None:
        for fault in list(self.faults):
            kind, _, rest = fault.partition(":")
            if kind == "moved_row" and self._once(fault):
                file_id, folder_id = rest.split(":")
                files[file_id].update(folder_id=folder_id, updated_at=_now(), updated_by=FOREIGN_USER)
            elif kind == "foreign_change" and self._once(fault):
                files[rest].update(tags=(files[rest].get("tags") or "") + ",touched-by-another-team", updated_at=_now(), updated_by=FOREIGN_USER)

    def _raise(self, args: dict[str, Any]) -> dict[str, Any]:
        """escalations.raise: hands the session to a listed person and stops the agent on it, so a second raise on
        the same session is refused."""
        sessions = self.tables.get("AgentSession", {})
        if args["session_id"] not in sessions:
            raise RpcError(-32602, "Record not found", "not_found")
        if args["assignee_party_id"] not in self.assignees:
            raise RpcError(-32602, "Invalid tool arguments.", "invalid_arguments")
        escalations = self.tables.setdefault("AgentEscalation", {})
        if any(e.get("session_id") == args["session_id"] for e in escalations.values()):
            raise RpcError(-32602, "Invalid tool arguments.", "session_already_escalated")
        row = {"id": str(uuid.uuid4()), "session_id": args["session_id"], "assignee_party_id": args["assignee_party_id"],
               "reason": args["reason"], "reason_code": args.get("reason_code") or "unresolved_after_retries",
               "status": "open", "created_by": self.me["id"], "created_at": _now(), "updated_at": _now()}
        escalations[row["id"]] = row
        self.write_log.append({"tool": RAISE_TOOL, "id": row["id"], "args": args, "by": self.me["id"]})
        return {"status": "ok", "result": {"ok": True, "escalation": copy.deepcopy(row)}}

    def _create(self, entity: str, table: dict[str, dict[str, Any]], args: dict[str, Any]) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), **args, "created_by": self.me["id"], "created_at": _now(), "updated_at": _now()}
        if entity == "AgentEscalation":
            row.setdefault("status", "open")
        table[row["id"]] = row
        self.write_log.append({"tool": f"{entity}.create", "id": row["id"], "args": args, "by": self.me["id"]})
        return copy.deepcopy(row)


def _loose_eq(actual: Any, wanted: Any) -> bool:
    if isinstance(wanted, bool) or str(wanted).lower() in ("true", "false"):
        return bool(actual) == (str(wanted).lower() == "true" if not isinstance(wanted, bool) else wanted)
    return str(actual) == str(wanted)
