"""Shared test doubles and fixtures."""

import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import config
from config import ServerSpec

FAKE_SERVER = Path(__file__).parent / "fixtures" / "fake_server.py"


@asynccontextmanager
async def started(specs):
    """Start a real ToolRouter and always close it, within the caller's task (anyio requirement)."""
    from mcp_clients import ToolRouter

    r = ToolRouter(specs)
    try:
        await r.start()
        yield r
    finally:
        await r.close()


def fake_spec() -> ServerSpec:
    """Spec for the stdio fake server, named 'fake'."""
    return ServerSpec(
        "fake",
        "stdio",
        command=sys.executable,
        args=[str(FAKE_SERVER)],
        allowed_tools={"echo", "fail", "slow", "big", "structured", "inject", "write_note"},
        write_tools={"write_note"},
    )


def fs_spec(ws) -> ServerSpec:
    """Real Filesystem server spec rooted at the given directory instead of the demo workspace."""
    spec = config.files_spec()
    return ServerSpec(spec.name, spec.transport, spec.allowed_tools, spec.write_tools, spec.command,
                      spec.args[:-1] + [str(ws)])


def call(id: str, name: str, args: str = "{}") -> NS:
    """Build an OpenAI-style tool call."""
    return NS(id=id, function=NS(name=name, arguments=args))


def reply(content: str | None = None, calls: list | None = None) -> NS:
    """Build an OpenAI-style chat completion with fake token usage."""
    return NS(
        choices=[NS(message=NS(content=content, tool_calls=calls))],
        usage=NS(prompt_tokens=10, completion_tokens=5),
    )


class ScriptedLLM:
    """Fake chat.completions.create that returns queued replies and records requests."""

    def __init__(self, replies: list):
        """Queue the replies and expose the .chat.completions.create shape."""
        self.replies, self.requests = list(replies), []
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kwargs):
        """Record the request (messages copied) and pop the next reply."""
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self.replies:
            raise AssertionError("ScriptedLLM called more times than scripted")
        return self.replies.pop(0)


class FakeConnection:
    """Duck-types MCPConnection with canned tools and results."""

    def __init__(self, spec, tools=(), results=None, exc=None):
        """Configure tool names, per-tool CallToolResults and an optional exception."""
        self.spec, self.tool_names, self.results, self.exc = (
            spec,
            tools,
            results or {},
            exc,
        )
        self.calls = []

    async def connect(self, stack):
        """Raise the configured exception, if any."""
        if self.exc:
            raise self.exc

    async def list_tools(self):
        """Return tool stubs."""
        return [
            NS(name=n, description="d", input_schema={"type": "object"})
            for n in self.tool_names
        ]

    async def call_tool(self, name, args):
        """Record the call and return the canned result."""
        self.calls.append((name, args))
        if self.exc:
            raise self.exc
        return self.results[name]


def result(text="ok", structured=None, is_error=False, blocks=None) -> NS:
    """Build a CallToolResult-shaped object."""
    content = blocks if blocks is not None else [NS(type="text", text=text)]
    return NS(content=content, structured_content=structured, is_error=is_error)


@pytest.fixture
def auto_approve():
    """ask callable that approves."""
    return lambda prompt: "y"


@pytest.fixture
def auto_deny():
    """ask callable that denies."""
    return lambda prompt: "n"


@pytest.fixture
def recording_ask():
    """ask callable that records prompts and approves."""
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return "y"

    ask.prompts = prompts
    return ask


@pytest.fixture
def tmp_workspace(tmp_path):
    """Temp workspace containing a copy of the demo package.json."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    shutil.copy(Path(config.WORKSPACE) / "package.json", ws / "package.json")
    return ws


@pytest.fixture
def injected_workspace(tmp_path):
    """Temp workspace whose package.json carries the planted prompt-injection note."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    shutil.copy(Path(__file__).parent / "fixtures" / "injected_package.json", ws / "package.json")
    return ws
