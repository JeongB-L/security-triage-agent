"""Unit tests for schema conversion and ToolRouter using fake connections."""

from types import SimpleNamespace as NS

import pytest

from config import ServerSpec
from errors import ServerError
from mcp_clients import ToolRouter, to_openai_schema
from tests.conftest import FakeConnection, result


def make_router(conns: dict[str, FakeConnection]) -> ToolRouter:
    """Build a router and swap in fake connections."""
    specs = [
        ServerSpec(n, "stdio", allowed_tools=set(c.tool_names))
        for n, c in conns.items()
    ]
    r = ToolRouter(specs)
    r.conns = conns
    return r


# ---- schema conversion ----


def test_schema_prefix_and_passthrough():
    """Name is prefixed; description and nested/required schema are preserved."""
    schema = {
        "type": "object",
        "properties": {"a": {"type": "array", "items": {"type": "string"}}},
        "required": ["a"],
    }
    out = to_openai_schema("srv", NS(name="t", description="desc", input_schema=schema))
    assert out == {
        "type": "function",
        "function": {"name": "srv__t", "description": "desc", "parameters": schema},
    }


def test_schema_defaults():
    """Missing description becomes '' and missing schema becomes an empty object schema."""
    f = to_openai_schema("s", NS(name="t", description=None, input_schema=None))[
        "function"
    ]
    assert f["description"] == "" and f["parameters"] == {
        "type": "object",
        "properties": {},
    }


# ---- routing ----


async def test_routes_by_prefix_and_splits_on_first_separator():
    """Calls go to the right connection; only the first __ splits server from tool."""
    a = FakeConnection(None, ["x"], {"x": result("from a")})
    b = FakeConnection(None, ["y__z"], {"y__z": result("from b")})
    r = make_router({"a": a, "b": b})
    assert await r.call("a__x", {"k": 1}) == "from a" and a.calls == [("x", {"k": 1})]
    assert await r.call("b__y__z", {}) == "from b" and b.calls == [("y__z", {})]


@pytest.mark.parametrize("name", ["nope__x", "a__nope", "noseparator", "a__"])
async def test_unknown_tool(name):
    """Unknown server, tool or malformed name returns an error string."""
    r = make_router({"a": FakeConnection(None, ["x"], {"x": result()})})
    assert await r.call(name, {}) == "ERROR: unknown tool"


async def test_is_error_result():
    """isError results are prefixed with ERROR:."""
    r = make_router(
        {"a": FakeConnection(None, ["x"], {"x": result("bad", is_error=True)})}
    )
    assert await r.call("a__x", {}) == "ERROR: bad"


async def test_exception_becomes_error_string():
    """An exception in call_tool never propagates."""
    c = FakeConnection(None, ["x"], {"x": result()})
    r = make_router({"a": c})
    c.exc = RuntimeError("down")
    assert (await r.call("a__x", {})).startswith("ERROR: RuntimeError: down")


async def test_structured_preferred_over_text():
    """structured_content wins and is rendered as JSON."""
    r = make_router(
        {"a": FakeConnection(None, ["x"], {"x": result("text", structured={"k": 1})})}
    )
    assert await r.call("a__x", {}) == '{"k": 1}'


async def test_text_blocks_joined_and_non_text_replaced():
    """Text blocks are joined; other blocks become a short placeholder."""
    blocks = [
        NS(type="text", text="one"),
        NS(type="image"),
        NS(type="text", text="two"),
    ]
    r = make_router({"a": FakeConnection(None, ["x"], {"x": result(blocks=blocks)})})
    assert await r.call("a__x", {}) == "one\n[image content omitted]\ntwo"


# ---- start ----


async def test_start_filters_to_allowlist():
    """Only allowlisted tools reach openai_tools; counts are reported."""
    c = FakeConnection(None, ["keep", "drop"])
    r = make_router({"a": c})
    r.allowed = {"a": {"keep"}}
    assert await r.start() == {"a": 1}
    assert [t["function"]["name"] for t in r.openai_tools] == ["a__keep"]
    assert await r.list_all_tools() == {"a": ["keep", "drop"]}
    await r.close()


async def test_failing_server_named_in_error():
    """A server that fails to connect raises a ServerError naming it, with the cause."""
    r = make_router({"broken": FakeConnection(None, [], exc=OSError("refused"))})
    with pytest.raises(
        ServerError, match="'broken' failed to start: OSError: refused"
    ) as info:
        await r.start()
    assert info.value.server == "broken"


async def test_exception_group_is_flattened_to_its_cause():
    """Transport failures arrive as exception groups; the message shows the real cause."""
    r = make_router(
        {
            "a": FakeConnection(
                None, [], exc=ExceptionGroup("tg", [OSError("connection refused")])
            )
        }
    )
    with pytest.raises(ServerError, match="OSError: connection refused"):
        await r.start()


async def test_missing_allowlisted_tool_fails_loudly_when_strict():
    """If a server stops offering an allowlisted tool, start() raises and names the tool."""
    r = make_router({"a": FakeConnection(None, ["keep"])})
    r.allowed = {"a": {"keep", "renamed"}}
    with pytest.raises(
        ServerError, match="no longer offers allowlisted tool.*renamed.*--list-tools"
    ):
        await r.start()


async def test_missing_allowlisted_tool_is_tolerated_when_not_strict():
    """--list-tools starts non-strict, so it still works after a tool rename."""
    r = make_router({"a": FakeConnection(None, ["keep"])})
    r.allowed = {"a": {"keep", "renamed"}}
    assert await r.start(strict=False) == {"a": 1}


async def test_list_all_tools_failure_is_a_server_error():
    """A server that cannot list its tools raises a ServerError naming it."""
    c = FakeConnection(None, ["x"])
    r = make_router({"a": c})

    async def boom():
        raise RuntimeError("down")

    c.list_tools = boom
    with pytest.raises(
        ServerError, match="'a' could not list tools: RuntimeError: down"
    ):
        await r.list_all_tools()


async def test_close_never_masks_the_real_error(capsys):
    """A failure while closing connections is reported as a warning, not raised."""

    class BadStack:
        async def aclose(self):
            raise RuntimeError("cleanup broke")

    r = make_router({"a": FakeConnection(None, ["x"])})
    r.stack = BadStack()
    await r.close()
    assert (
        "problem while closing MCP connections: RuntimeError: cleanup broke"
        in capsys.readouterr().err
    )
