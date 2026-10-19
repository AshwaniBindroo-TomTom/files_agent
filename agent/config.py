"""Configuration: .env loading, instance URLs, verified ids and limits.

No secret is hard-coded here. Passwords and API keys come from `.env` (see
`.env.example`) or the process environment, which always wins.
"""
from __future__ import annotations

import functools
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agent.redact import Secret, is_sensitive_key

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
RUNS_DIR = REPO_ROOT / "runs"

INSTANCES = {
    "keystone": "https://class.agentswitch.theschoolofai.in",
    "suryodaya": "https://agentswitch.theschoolofai.in",
}
WRITE_BUSINESS = "keystone"  # live writes are only ever allowed here

# Keystone ids, verified live on 22 Sept 2026.
KEYSTONE_INCOMING_FOLDER = "6f8a3ed1-f2df-46a7-8dcb-275e9494c799"
KEYSTONE_SEAT_ID = "a9e75bbc-cf2c-4d03-bb96-9cc6d57d9754"

# The 9 Incoming rows. Live writes can never touch any other file id.
KEYSTONE_INCOMING_ALLOWLIST = frozenset({
    "680e8af6-15f3-49c6-b70b-987316fa5775",  # Cert_MillCert_SS304_Heat90114.pdf
    "8018a70b-47d5-472b-88b6-b1ced1ced8b0",  # IMG_20260814_093214.jpg
    "b45cecdd-9f14-491a-a0ee-2826d3fabb15",  # J-KNOB-09_RevA.dxf
    "82f83d94-5a46-4df3-9ee1-61e8b3c79d6e",  # PO_4471_ApexMetals_signed (1).pdf
    "732439a0-7f36-4d31-ac3b-f406c41c00bd",  # PO_4471_ApexMetals_signed.pdf
    "60f685c9-bcb5-43a3-a403-18b7e9d76368",  # Untitled.pdf
    "81857de6-e6e9-41c5-9da8-67cb5d1903c1",  # W9_JMillerWelding_2026.pdf
    "b1d3894c-12e9-4ee1-b1da-82c7191ed4a0",  # scan0042.pdf
    "1ee27946-7064-42e1-afce-376068a545bf",  # timesheet_week33.xlsx
})

# The only write tools the agent may call. Escalations: raise since 15 Oct 2026, create before it.
WRITE_TOOLS = frozenset({"FileAttachment.update", "AgentSession.create", "AgentEscalation.create",
                         "endpoint.agent_governance.escalations.raise"})

# MCP tools the agent needs; checked at start-up (value = required args).
REQUIRED_TOOLS = {
    "FileAttachment.list": [],
    "FileAttachment.get": ["id"],
    "FileAttachment.update": ["id"],
    "DriveFolder.list": [],
    "Item.list": [],
    "Party.list": [],
    "DriveAccessLog.list": [],
    "AgentSession.create": [],
    "AgentEscalation.list": [],
}

# The tools that hand a file to a person. All optional: the agent uses create while the platform offers it, else
# raise (+ assignees), which replaced create on Keystone on 15 Oct. With neither, or with no person to assign,
# the run goes on and each file that needed a person is recorded (cannot_escalate), left in place and named in the answer.
ESCALATION_TOOL = "AgentEscalation.create"
RAISE_TOOL = "endpoint.agent_governance.escalations.raise"
ASSIGNEES_TOOL = "endpoint.agent_governance.escalations.assignees"
OPTIONAL_TOOLS = {
    RAISE_TOOL: ["session_id", "assignee_party_id", "reason"],
    ASSIGNEES_TOOL: [],
    ESCALATION_TOOL: ["session_id", "reason"],
}
ESCALATION_WRITE_TOOLS = frozenset({ESCALATION_TOOL, RAISE_TOOL})

# Read-only MCP tools the model may call directly (keeps the prompt small).
EXPOSED_READ_TOOLS = (
    "FileAttachment.list", "FileAttachment.get", "DriveFolder.list", "Item.list",
    "Party.list", "DriveAccessLog.list", "AgentEscalation.list", "tools.search",
)


QUOTED_VALUE_RE = re.compile(r"""^(['"])(.*?)\1(\s+#.*)?$""")


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse KEY=VALUE lines (`export KEY=VALUE` too). The process environment overrides the file."""
    values: dict[str, str] = {}
    if path.exists():
        _warn_if_shared(path)
        # STRIDE D10: a BOM, an `export ` prefix or a trailing comment must not hide a key such as a lowered AS_MAX_USD.
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip().removeprefix("export ").strip()] = _env_value(value)
    merged = {**values, **{k: v for k, v in os.environ.items() if k.startswith(("AS_", "ANTHROPIC_", "AGENTSWITCH_", "OPENAI_"))}}
    # STRIDE I4: a password or key stays masked in any traceback that prints this dict
    return {k: Secret(v) if is_sensitive_key(k) else v for k, v in merged.items()}


def _env_value(raw: str) -> str:
    """Remove one matching pair of quotes; an unquoted value ends where ' #' starts a comment."""
    value = raw.strip()
    quoted = QUOTED_VALUE_RE.match(value)
    if quoted:
        return quoted.group(2)
    return re.split(r"\s+#", value, maxsplit=1)[0]


@functools.cache
def _warn_if_shared(path: Path) -> None:
    """Warn once per file when other local users can read or change it (POSIX only)."""
    # STRIDE I10: .env holds the seat password and the model key; Windows st_mode can't tell, so skip it there.
    if os.name == "posix" and path.stat().st_mode & 0o077:
        print(f"warning: other users on this machine can read or change {path}; run: chmod 600 {path}", file=sys.stderr)


def _amount(env: dict[str, str], name: str, default: str, *, positive: bool = False) -> float:
    """A dollar cap or price. nan, inf or a negative value (or 0 for a price) would switch the $ cap off."""
    raw = env.get(name, default)
    value = float(raw)
    # STRIDE D10: fail closed instead of running with no dollar stop.
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be a finite number {'above' if positive else 'of at least'} 0 (got {raw!r})")
    return value


@dataclass(frozen=True)
class Settings:
    business: str
    base_url: str
    email: str
    # STRIDE I4: keep the password and model key out of repr(), so print(rt), pytest -vv or --locals can't show them.
    password: str = field(repr=False)
    anthropic_api_key: str = field(repr=False)
    model: str
    max_turns: int
    max_mcp_calls: int
    max_usd: float
    price_in_per_mtok: float
    price_out_per_mtok: float
    allow_writes: bool
    actor_kind: str
    # The official server run (Release 8.1) injects a ready bearer token instead of a password.
    token: str = field(repr=False, default="")

    def secrets(self) -> list[str]:
        return [s for s in (self.password, self.anthropic_api_key, self.token) if s]


def get_settings(business: str = "keystone", env: dict[str, str] | None = None) -> Settings:
    if business not in INSTANCES:
        raise ValueError(f"Unknown business {business!r}; use one of {sorted(INSTANCES)}")
    explicit_env = env
    env = load_env() if env is None else env
    actor_kind = env.get("AS_ACTOR_KIND", "").strip()
    if actor_kind and actor_kind not in ("user", "system"):
        raise ValueError("AS_ACTOR_KIND must be 'user', 'system' or blank ('agent' is not allowed by the platform)")
    return Settings(
        business=business,
        # The official server run points at a fresh copy of the instance; its URL wins over the known one.
        base_url=(env.get("AGENTSWITCH_BASE_URL") or INSTANCES[business]).rstrip("/"),
        email=env.get("AS_EMAIL", "team20@theschoolofai.in"),
        password=Secret(env.get(f"AS_{business.upper()}_PASSWORD", "")),
        anthropic_api_key=Secret(env.get("ANTHROPIC_API_KEY", "")),
        model=env.get("AS_MODEL", "claude-sonnet-5"),
        max_turns=int(env.get("AS_MAX_TURNS", "12")),
        max_mcp_calls=int(env.get("AS_MAX_MCP_CALLS", "80")),  # a 9-file tidy uses ~28; ~3 per file moved
        max_usd=_amount(env, "AS_MAX_USD", "0.50"),
        price_in_per_mtok=_amount(env, "AS_PRICE_IN_PER_MTOK", "3.0", positive=True),
        price_out_per_mtok=_amount(env, "AS_PRICE_OUT_PER_MTOK", "15.0", positive=True),
        # The second write switch is read from the live shell only, never from .env, so it can't stay on by accident.
        allow_writes=(os.environ if explicit_env is None else explicit_env).get("AS_ALLOW_WRITES", "0") == "1",
        actor_kind=actor_kind,
        token=Secret(env.get("AGENTSWITCH_TOKEN", "")),
    )
