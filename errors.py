"""Error model: the agent's exception hierarchy and the global handler that reports it."""

import sys


class AgentError(Exception):
    """Base class for every error the agent raises on purpose; the global handler prints these as one line."""


class ConfigError(AgentError):
    """Invalid or missing configuration (bad .env value, missing API key)."""


class InputError(AgentError, ValueError):
    """The user's prompt or command line is unusable."""


class ServerError(AgentError):
    """An MCP server failed to start, no longer offers an allowlisted tool, or could not be listed."""

    def __init__(self, server: str, detail: str):
        """Name the failing server in the message and keep it on the exception."""
        super().__init__(f"MCP server '{server}' {detail}")
        self.server = server


class LLMError(AgentError):
    """The LLM call failed, or the provider returned a response the agent cannot use."""


class LimitError(AgentError):
    """The run hit its step cap or token budget without a final answer."""


class ToolFailureError(AgentError):
    """Too many tool calls failed in a row, so the run was stopped instead of looping."""


def describe(exc: BaseException) -> str:
    """One readable line for an exception, flattening exception groups from async transports."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(describe(e) for e in exc.exceptions)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _handle(exc_type, exc, tb) -> None:
    """Global handler: AgentError and Ctrl+C get one clear line; any other exception is a bug, so keep its traceback."""
    if issubclass(exc_type, AgentError):
        print(f"Error: {exc}", file=sys.stderr)
    elif issubclass(exc_type, KeyboardInterrupt):
        print("Interrupted.", file=sys.stderr)
    else:
        sys.__excepthook__(exc_type, exc, tb)


def install_excepthook() -> None:
    """Make _handle the process-wide handler for uncaught exceptions (also covers errors raised at import)."""
    sys.excepthook = _handle
