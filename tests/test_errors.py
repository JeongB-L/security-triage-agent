"""Unit tests for the error model: hierarchy, readable descriptions, and the global handler."""

import sys

import pytest

import errors
from errors import (AgentError, ConfigError, InputError, LimitError, LLMError, ServerError,
                    ToolFailureError, describe)


@pytest.mark.parametrize("cls", [ConfigError, InputError, LLMError, LimitError, ToolFailureError])
def test_every_agent_error_shares_one_base(cls):
    """All deliberate errors derive from AgentError, so one handler can report them."""
    assert issubclass(cls, AgentError) and not issubclass(cls, KeyboardInterrupt)


def test_server_error_names_the_server():
    """ServerError puts the server name in the message and on the exception."""
    e = ServerError("security", "failed to start: boom")
    assert str(e) == "MCP server 'security' failed to start: boom" and e.server == "security"


def test_input_error_is_also_a_value_error():
    """InputError stays catchable as ValueError for callers that expect it."""
    assert issubclass(InputError, ValueError)


def test_describe_flattens_exception_groups():
    """Nested exception groups become one readable line of their leaf errors."""
    group = ExceptionGroup("outer", [OSError("refused"), ExceptionGroup("inner", [TimeoutError("slow")])])
    assert describe(group) == "OSError: refused; TimeoutError: slow"
    assert describe(TimeoutError()) == "TimeoutError"


def test_handler_prints_agent_errors_as_one_line(capsys):
    """AgentError is reported as 'Error: message' with no traceback."""
    errors._handle(ConfigError, ConfigError("bad value"), None)
    assert capsys.readouterr().err == "Error: bad value\n"


def test_handler_reports_ctrl_c_cleanly(capsys):
    """Ctrl+C prints a short notice."""
    errors._handle(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert capsys.readouterr().err == "Interrupted.\n"


def test_handler_keeps_tracebacks_for_unexpected_bugs(capsys):
    """Anything that is not an AgentError is a bug, so the normal traceback is kept."""
    try:
        raise ZeroDivisionError("bug")
    except ZeroDivisionError:
        errors._handle(*sys.exc_info())
    err = capsys.readouterr().err
    assert "Traceback" in err and "ZeroDivisionError: bug" in err


def test_install_sets_the_global_hook(monkeypatch):
    """install_excepthook makes the handler process-wide."""
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    errors.install_excepthook()
    assert sys.excepthook is errors._handle
