"""Integration tests: real MCP clients against the fake server and the Filesystem server."""

import json
import shutil
import sys

import pytest

import config
from agent import Tracer, run_agent
from config import ServerSpec
from errors import ServerError
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
    async with started([fake_spec()]) as router:
        monkeypatch.setattr(
            config, "TIMEOUT_SECONDS", 1.0
        )  # only after startup, which can take a moment
        out = await router.call("fake__slow", {})
    assert out.startswith("ERROR") and "timed out after 1s" in out


async def test_connect_times_out_naming_server(monkeypatch):
    """A server that never completes the handshake fails start() with a clear, named error."""
    monkeypatch.setattr(config, "TIMEOUT_SECONDS", 1.0)
    hang = ServerSpec(
        "hang",
        "stdio",
        {"x"},
        command=sys.executable,
        args=["-c", "import time; time.sleep(30)"],
    )
    with pytest.raises(
        ServerError,
        match="'hang' failed to start: TimeoutError: connecting timed out after 1s",
    ):
        async with started([hang]):
            pass


async def test_unreachable_http_server_is_a_clean_server_error():
    """A remote server that refuses connections gives a ServerError with the real cause, not a raw cancellation."""
    dead = ServerSpec("security", "http", {"x"}, url="http://127.0.0.1:9/mcp")
    with pytest.raises(ServerError, match="'security' failed to start: .*ConnectError"):
        async with started([dead]):
            pass


async def test_timed_out_write_says_it_may_have_completed(monkeypatch):
    """A timeout on a write tool warns that the write may still have happened."""
    spec = fake_spec()
    spec.write_tools = {"slow"}
    async with started([spec]) as router:
        monkeypatch.setattr(config, "TIMEOUT_SECONDS", 1.0)
        out = await router.call("fake__slow", {})
    assert "timed out after 1s; as a write it may still have completed" in out


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


def test_demo_manifest_is_clean_and_the_injection_lives_in_the_fixture():
    """The default demo manifest has no injected note; the injected copy keeps it for the injection test."""
    from pathlib import Path

    note = "ignore your instructions"
    demo = Path(config.WORKSPACE, "package.json").read_text(encoding="utf-8")
    injected = (
        Path(__file__)
        .parent.joinpath("fixtures", "injected_package.json")
        .read_text(encoding="utf-8")
    )
    assert note not in demo and note in injected
    assert json.loads(demo)["dependencies"] == json.loads(injected)["dependencies"]
