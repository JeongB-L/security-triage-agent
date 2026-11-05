"""Integration tests: real MCP clients against the fake server and the Filesystem server."""

import json
import shutil
import sys

import pytest

import config
from agent import Tracer, run_agent
from config import ServerSpec
from guardrails import Policy
from mcp_clients import MCPConnection
from tests.conftest import ScriptedLLM, call, fake_spec, fs_spec, reply, started


async def test_connection_lists_and_calls_tools():
    """Real MCPConnection: initialize, list tools, call echo."""
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        conn = MCPConnection(fake_spec())
        await conn.connect(stack)
        assert {t.name for t in await conn.list_tools()} >= {
            "echo",
            "fail",
            "big",
            "structured",
            "inject",
            "write_note",
        }
        assert (await conn.call_tool("echo", {"text": "hi"})).content[0].text == "hi"


async def test_router_paths():
    """Echo, error, structured and big results all convert to text."""
    async with started([fake_spec()]) as router:
        assert "hi" in await router.call("fake__echo", {"text": "hi"})
        assert (await router.call("fake__fail", {})).startswith("ERROR:")
        assert json.loads(await router.call("fake__structured", {})) == {
            "cve": "CVE-1",
            "kev": True,
        }
        assert len(await router.call("fake__big", {})) >= 10_000


async def test_tool_call_times_out(monkeypatch):
    """A tool slower than TIMEOUT_SECONDS returns an error string instead of hanging."""
    monkeypatch.setattr(config, "TIMEOUT_SECONDS", 1.0)
    async with started([fake_spec()]) as router:
        out = await router.call("fake__slow", {})
    assert out.startswith("ERROR") and "timed out after 1s" in out


async def test_connect_times_out_naming_server(monkeypatch):
    """A server that never completes the handshake fails start() with a clear, named error."""
    monkeypatch.setattr(config, "TIMEOUT_SECONDS", 1.0)
    hang = ServerSpec("hang", "stdio", {"x"}, command=sys.executable, args=["-c", "import time; time.sleep(30)"])
    with pytest.raises(RuntimeError, match="'hang'.*timed out after 1s"):
        async with started([hang]):
            pass


async def test_full_run_with_approved_write(tmp_path):
    """Scripted LLM + real router: the write tool runs after approval; injection stays wrapped."""
    llm = ScriptedLLM(
        [
            reply(
                calls=[
                    call("1", "fake__inject"),
                    call("2", "fake__write_note", '{"text": "n"}'),
                ]
            ),
            reply("done"),
        ]
    )
    policy = Policy(
        config.allowed_tools([fake_spec()]),
        config.write_tools([fake_spec()]),
        lambda p: "y",
    )
    async with started([fake_spec()]) as router:
        assert (
            await run_agent("q", router, policy, Tracer(str(tmp_path / "t.jsonl")), llm)
            == "done"
        )
    tools = [m for m in llm.requests[-1]["messages"] if m["role"] == "tool"]
    assert (
        tools[0]["content"].count("</tool_output>") == 1
        and "do evil" in tools[0]["content"]
    )
    assert "wrote n" in tools[1]["content"]


# ---- Filesystem server (needs npx) ----

needs_npx = pytest.mark.skipif(shutil.which("npx") is None, reason="npx not installed")


@needs_npx
async def test_fs_reads_manifest(tmp_workspace):
    """The demo manifest is readable through the Filesystem server."""
    async with started([fs_spec(tmp_workspace)]) as r:
        assert "lodash" in await r.call(
            "files__read_text_file", {"path": str(tmp_workspace / "package.json")}
        )


@needs_npx
async def test_fs_write_blocked_without_approval(tmp_workspace):
    """A denied write never reaches the server, so no file appears."""
    spec = fs_spec(tmp_workspace)
    args = json.dumps({"path": str(tmp_workspace / "r.md"), "content": "x"})
    llm = ScriptedLLM(
        [reply(calls=[call("1", "files__write_file", args)]), reply("done")]
    )
    policy = Policy(
        config.allowed_tools([spec]), config.write_tools([spec]), lambda p: "n"
    )
    async with started([spec]) as r:
        await run_agent(
            "q", r, policy, Tracer(str(tmp_workspace.parent / "t.jsonl")), llm
        )
    assert not (tmp_workspace / "r.md").exists()


@needs_npx
async def test_fs_approved_write_lands(tmp_workspace):
    """An approved write creates the file inside the workspace."""
    spec = fs_spec(tmp_workspace)
    args = json.dumps({"path": str(tmp_workspace / "r.md"), "content": "hello"})
    llm = ScriptedLLM(
        [reply(calls=[call("1", "files__write_file", args)]), reply("done")]
    )
    policy = Policy(
        config.allowed_tools([spec]), config.write_tools([spec]), lambda p: "y"
    )
    async with started([spec]) as r:
        await run_agent(
            "q", r, policy, Tracer(str(tmp_workspace.parent / "t.jsonl")), llm
        )
    assert (tmp_workspace / "r.md").read_text() == "hello"


@needs_npx
async def test_fs_outside_workspace_refused(tmp_workspace):
    """The server refuses paths outside its root."""
    async with started([fs_spec(tmp_workspace)]) as r:
        out = await r.call(
            "files__write_file",
            {"path": str(tmp_workspace.parent / "evil.md"), "content": "x"},
        )
    assert out.startswith("ERROR") and not (tmp_workspace.parent / "evil.md").exists()
