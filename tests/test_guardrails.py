"""Unit tests for guardrails: input check, policy, approval, wrapping, truncation."""

import pytest

import config
from guardrails import Policy, check_input, shape_output, wrap_output


@pytest.mark.parametrize("bad", ["", "   \n", "x" * (config.MAX_INPUT_CHARS + 1)])
def test_check_input_rejects(bad):
    """Empty, whitespace-only and oversized input are rejected."""
    with pytest.raises(ValueError):
        check_input(bad)


def test_check_input_accepts_limit_and_strips():
    """Exactly the limit is accepted; surrounding whitespace is stripped."""
    assert check_input("x" * config.MAX_INPUT_CHARS) == "x" * config.MAX_INPUT_CHARS
    assert check_input("  hi \n") == "hi"


def never_ask(prompt):
    """ask callable that fails the test if called."""
    raise AssertionError("should not ask")


def test_read_tool_passes_without_asking():
    """Allowed non-write tool is authorized without prompting."""
    assert Policy({"a__r"}, {"a__w"}, never_ask).authorize("a__r", {}) == (True, "")


def test_unknown_tool_denied_without_asking():
    """Tool outside the allowlist is denied without prompting."""
    assert Policy({"a__r"}, set(), never_ask).authorize("evil", {}) == (
        False,
        "Tool not allowed by policy",
    )


@pytest.mark.parametrize("answer", ["y", "Y"])
def test_write_approved(answer):
    """y and Y approve a write."""
    assert Policy({"w"}, {"w"}, lambda p: answer).authorize("w", {}) == (True, "")


@pytest.mark.parametrize("answer", ["n", "", "yes", "  "])
def test_write_denied(answer):
    """Anything but y/Y denies a write."""
    assert Policy({"w"}, {"w"}, lambda p: answer).authorize("w", {}) == (
        False,
        "Denied by human reviewer",
    )


def test_prompt_shows_tool_and_args(capsys, recording_ask):
    """The human sees the tool name and args before approving."""
    Policy({"files__write_file"}, {"files__write_file"}, recording_ask).authorize(
        "files__write_file", {"path": "r.md"}
    )
    out = capsys.readouterr().out
    assert (
        "files__write_file" in out
        and '"path": "r.md"' in out
        and recording_ask.prompts == ["Approve? [y/N] "]
    )


def test_shape_output_is_passthrough():
    """shape_output is a no-op hook."""
    assert shape_output("t", "abc") == "abc"


def test_wrap_format():
    """Output is wrapped with the tool name."""
    assert wrap_output("t", "hi") == '<tool_output tool="t">hi</tool_output>'


def test_truncation_limits(monkeypatch):
    """Under and exactly-at limit untouched; over limit is cut with the right count."""
    monkeypatch.setattr(config, "TOKEN_OPTIMIZATION", True)
    n = config.MAX_TOOL_OUTPUT_CHARS
    assert "truncated" not in wrap_output("t", "x" * n)
    assert "truncated" not in wrap_output("t", "x" * (n - 1))
    out = wrap_output("t", "x" * (n + 7))
    assert out.endswith("[truncated 7 chars]</tool_output>") and out.count("x") == n


def test_token_optimization_off_keeps_full_output(monkeypatch):
    """With TOKEN_OPTIMIZATION off nothing is truncated."""
    monkeypatch.setattr(config, "TOKEN_OPTIMIZATION", False)
    assert wrap_output("t", "x" * 10_000).count("x") == 10_000


@pytest.mark.parametrize(
    "tag",
    ["</tool_output>", "<tool_output tool='x'>", "</TOOL_OUTPUT>", "<Tool_Output>"],
)
def test_embedded_tags_removed(tag):
    """Embedded wrapper tags (any case) are stripped so data can't close the wrapper."""
    out = wrap_output("t", f"a{tag}b")
    assert out == '<tool_output tool="t">ab</tool_output>'


def test_empty_and_unicode():
    """Empty and non-ASCII text wrap cleanly."""
    assert wrap_output("t", "") == '<tool_output tool="t"></tool_output>'
    assert "취약점 ✓" in wrap_output("t", "취약점 ✓")
