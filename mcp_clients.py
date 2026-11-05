"""MCP client layer: one client per server (stdio or streamable HTTP) plus the tool router."""

import asyncio
import json
import sys
from contextlib import AsyncExitStack, asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

import config
from config import ServerSpec
from errors import ServerError, describe


@asynccontextmanager
async def _limit(what: str, note: str = ""):
    """Fail with a clear TimeoutError if the block takes longer than config.TIMEOUT_SECONDS."""
    try:
        async with asyncio.timeout(config.TIMEOUT_SECONDS):
            yield
    except TimeoutError:
        raise TimeoutError(f"{what} timed out after {config.TIMEOUT_SECONDS:g}s{note}") from None


class MCPConnection:
    """One MCP client session connected to exactly one server."""

    def __init__(self, spec: ServerSpec):
        """Remember the spec; the session is opened in connect()."""
        self.spec = spec
        self.session: ClientSession | None = None

    async def connect(self, stack: AsyncExitStack) -> None:
        """Open the transport and session, then run the MCP initialize handshake (time-limited)."""
        # Everything opens in a private stack first. If the handshake fails, unwinding it here
        # surfaces the real cause (for example "connection refused") instead of a bare cancellation.
        async with AsyncExitStack() as opened:
            async with _limit("connecting"):
                if self.spec.transport == "stdio":
                    params = StdioServerParameters(
                        command=self.spec.command, args=self.spec.args, env=self.spec.env
                    )
                    read, write = await opened.enter_async_context(stdio_client(params))
                else:
                    read, write = await opened.enter_async_context(
                        streamable_http_client(self.spec.url)
                    )
                self.session = await opened.enter_async_context(ClientSession(read, write))
                await self.session.initialize()
            # Success: hand the open resources to the caller's stack so they close at shutdown.
            stack.push_async_callback(opened.pop_all().aclose)

    async def list_tools(self) -> list:
        """Return the server's raw tool list."""
        async with _limit("listing tools"):
            return (await self.session.list_tools()).tools

    async def call_tool(self, name: str, args: dict):
        """Call one tool and return the raw CallToolResult."""
        # A timed-out write is ambiguous: the server may still finish it, so say so.
        note = "; as a write it may still have completed" if name in self.spec.write_tools else ""
        async with _limit(f"tool '{name}'", note):
            return await self.session.call_tool(name, args)


def to_openai_schema(server: str, tool) -> dict:
    """Convert an MCP tool into an OpenAI function schema, prefixed with the server name."""
    return {
        "type": "function",
        "function": {
            "name": f"{server}__{tool.name}",
            "description": tool.description or "",
            "parameters": tool.input_schema or {"type": "object", "properties": {}},
        },
    }


def result_to_text(result) -> str:
    """Flatten a CallToolResult to text: structured JSON first, else joined text blocks."""
    if result.structured_content:
        text = json.dumps(result.structured_content)
    else:
        text = "\n".join(
            (
                b.text
                if getattr(b, "type", "") == "text"
                else f"[{getattr(b, 'type', 'unknown')} content omitted]"
            )
            for b in result.content
        )
    return f"ERROR: {text}" if result.is_error else text


class ToolRouter:
    """Owns all MCP connections; the only thing the agent host talks to."""

    def __init__(self, specs: list[ServerSpec] | None = None):
        """Create one connection per server spec (default: config.SERVERS)."""
        specs = specs or config.SERVERS
        self.conns = {s.name: MCPConnection(s) for s in specs}
        self.allowed = {s.name: s.allowed_tools for s in specs}
        self.openai_tools: list[dict] = []
        self.stack = AsyncExitStack()

    async def start(self, strict: bool = True) -> dict[str, int]:
        """Connect every server, keep only allowlisted tools, return tool count per server.

        Raises ServerError if a server fails, or (when strict) no longer offers an allowlisted tool.
        """
        counts = {}
        for name, conn in self.conns.items():
            try:
                await conn.connect(self.stack)
                offered = await conn.list_tools()
            except Exception as e:
                raise ServerError(name, f"failed to start: {describe(e)}") from e
            # A renamed or removed tool must be loud, not a silently weaker agent.
            missing = self.allowed[name] - {t.name for t in offered}
            if missing and strict:
                raise ServerError(
                    name,
                    f"no longer offers allowlisted tool(s): {', '.join(sorted(missing))} "
                    "(run --list-tools and update config.py)",
                )
            tools = [t for t in offered if t.name in self.allowed[name]]
            self.openai_tools += [to_openai_schema(name, t) for t in tools]
            counts[name] = len(tools)
        return counts

    async def call(self, prefixed_name: str, args: dict) -> str:
        """Route a prefixed tool call to its server; failures come back as text, never raise."""
        server, sep, tool = prefixed_name.partition("__")
        if not sep or tool not in self.allowed.get(server, ()):
            return "ERROR: unknown tool"
        try:
            return result_to_text(await self.conns[server].call_tool(tool, args))
        except Exception as e:
            return f"ERROR: {describe(e)}"

    async def list_all_tools(self) -> dict[str, list[str]]:
        """Raw, unfiltered tool names per server (for --list-tools)."""
        names = {}
        for name, conn in self.conns.items():
            try:
                names[name] = [t.name for t in await conn.list_tools()]
            except Exception as e:
                raise ServerError(name, f"could not list tools: {describe(e)}") from e
        return names

    async def close(self) -> None:
        """Close all sessions and transports; cleanup problems are reported but never mask the real error."""
        try:
            await self.stack.aclose()
        except Exception as e:
            print(f"Warning: problem while closing MCP connections: {describe(e)}", file=sys.stderr)
