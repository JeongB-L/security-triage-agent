"""Configuration: constants, env settings and MCP server specs (allowlists live with their server)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from errors import ConfigError

load_dotenv()


def _env_number(name: str, default, cast, minimum):
    """Read a numeric setting from the environment; fail fast with a clear message if it is invalid."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    return value


# LLM via OpenRouter (OpenAI-compatible); the key comes from .env only.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.getenv("MODEL", "openai/gpt-4.1-mini")
MAX_STEPS = 50
MAX_TOTAL_TOKENS = _env_number("MAX_TOTAL_TOKENS", 1_000_000, int, 1)
TIMEOUT_SECONDS = _env_number("TIMEOUT_SECONDS", 60.0, float, 0.1)  # MCP connect, MCP tool call, LLM call
LLM_MAX_RETRIES = 2  # the OpenAI SDK retries transient errors (429, 5xx, timeouts) this many times
MAX_CONSECUTIVE_TOOL_ERRORS = 5
MAX_TOOL_OUTPUT_CHARS = 3000  # cve_lookup puts the EPSS field at ~char 2100, so a lower cap would cut it
MAX_INPUT_CHARS = 2000
TRACE_PATH = str(Path(__file__).parent.resolve() / "trace.jsonl")
WORKSPACE = str(Path(__file__).parent.resolve() / "workspace")

TOKEN_OPTIMIZATION = os.getenv("TOKEN_OPTIMIZATION", "on").strip().lower() not in (
    "off",
    "0",
    "false",
    "no",
)


@dataclass
class ServerSpec:
    """How to reach one MCP server and which of its tools the agent may use."""

    name: str  # becomes the tool prefix
    transport: str  # "stdio" or "http"
    allowed_tools: set[str]  # unprefixed
    write_tools: set[str] = field(default_factory=set)  # unprefixed, approval-gated
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    url: str = ""


def security_spec() -> ServerSpec:
    """Datakoot Security Intel server spec (remote, streamable HTTP, no key)."""
    return ServerSpec(
        "security",
        "http",
        url="https://security.datakoot.com/mcp",
        allowed_tools={
            "cve_lookup",
            "epss_score",
            "package_vulnerabilities",
            "audit_dependencies",
        },
    )


def files_spec() -> ServerSpec:
    """Filesystem server spec, rooted at the workspace directory only."""
    return ServerSpec(
        "files",
        "stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", WORKSPACE],
        allowed_tools={"list_directory", "read_text_file", "write_file"},
        write_tools={"write_file"},
    )


SERVERS = [security_spec(), files_spec()]


def _prefixed(attr: str, servers: list[ServerSpec]) -> set[str]:
    """Collect one tool-set attribute across servers as server__tool names."""
    return {f"{s.name}__{t}" for s in servers for t in getattr(s, attr)}


def allowed_tools(servers: list[ServerSpec] | None = None) -> set[str]:
    """Prefixed allowlist for all servers."""
    return _prefixed("allowed_tools", servers or SERVERS)


def write_tools(servers: list[ServerSpec] | None = None) -> set[str]:
    """Prefixed set of approval-gated write tools."""
    return _prefixed("write_tools", servers or SERVERS)
