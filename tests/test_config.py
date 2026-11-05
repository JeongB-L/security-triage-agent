"""Unit tests for config: specs, prefixed allowlists, token switch."""

import importlib
import os

import pytest

import config


def test_allowlist_names_are_prefixed():
    """Every allowlisted name is server__tool."""
    expected = {f"{s.name}__{t}" for s in config.SERVERS for t in s.allowed_tools}
    assert config.allowed_tools() == expected


def test_write_tools_subset_of_allowed():
    """Write tools are always also allowed tools, per server and overall."""
    for s in config.SERVERS:
        assert s.write_tools <= s.allowed_tools
    assert config.write_tools() <= config.allowed_tools()
    assert "files__write_file" in config.write_tools()


def test_security_spec_is_datakoot_over_http():
    """Security server is the remote Datakoot endpoint with the four allowlisted tools."""
    spec = config.security_spec()
    assert spec.transport == "http" and spec.url == "https://security.datakoot.com/mcp"
    assert spec.allowed_tools == {"cve_lookup", "epss_score", "package_vulnerabilities", "audit_dependencies"}


def test_filesystem_spec_single_workspace_arg():
    """Filesystem server gets exactly the absolute workspace path as its last arg, over stdio."""
    spec = config.files_spec()
    assert spec.transport == "stdio" and spec.args[-1] == config.WORKSPACE
    assert os.path.isabs(config.WORKSPACE) and spec.args.count(config.WORKSPACE) == 1


@pytest.mark.parametrize(
    "value,expected",
    [("on", True), ("", True), ("off", False), ("OFF", False), ("0", False)],
)
def test_token_optimization_env(monkeypatch, value, expected):
    """TOKEN_OPTIMIZATION env toggles truncation; default is on."""
    monkeypatch.setenv("TOKEN_OPTIMIZATION", value)
    try:
        assert importlib.reload(config).TOKEN_OPTIMIZATION is expected
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_timeout_and_token_budget_env(monkeypatch):
    """TIMEOUT_SECONDS and MAX_TOTAL_TOKENS come from the environment; MAX_STEPS stays 50."""
    monkeypatch.setenv("TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("MAX_TOTAL_TOKENS", "123")
    try:
        reloaded = importlib.reload(config)
        assert (reloaded.TIMEOUT_SECONDS, reloaded.MAX_TOTAL_TOKENS, reloaded.MAX_STEPS) == (7.5, 123, 50)
    finally:
        monkeypatch.undo()
        importlib.reload(config)


@pytest.mark.parametrize(
    "name,value,message",
    [
        ("TIMEOUT_SECONDS", "abc", "TIMEOUT_SECONDS must be a number, got 'abc'"),
        ("TIMEOUT_SECONDS", "0", "TIMEOUT_SECONDS must be at least"),
        ("MAX_TOTAL_TOKENS", "-5", "MAX_TOTAL_TOKENS must be at least"),
        ("MAX_TOTAL_TOKENS", "1e6", "MAX_TOTAL_TOKENS must be a number"),
    ],
)
def test_invalid_numeric_settings_raise_config_error(monkeypatch, name, value, message):
    """Bad numeric env values fail fast with a ConfigError naming the setting."""
    from errors import ConfigError

    monkeypatch.setenv(name, value)
    try:
        with pytest.raises(ConfigError, match=message):
            importlib.reload(config)
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_trace_path_is_absolute_and_next_to_config():
    """TRACE_PATH does not depend on the current directory, just like WORKSPACE."""
    from pathlib import Path

    trace = Path(config.TRACE_PATH)
    assert trace.is_absolute() and trace.parent == Path(config.__file__).resolve().parent
    assert trace.name == "trace.jsonl"
