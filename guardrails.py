"""Policy layer: input check, tool allowlist, write approval, untrusted-output wrapping."""

import json
import re
from typing import Callable

import config


def check_input(text: str) -> str:
    """Strip the question; reject empty or oversized input."""
    text = text.strip()
    if not text:
        raise ValueError("Question is empty")
    if len(text) > config.MAX_INPUT_CHARS:
        raise ValueError(f"Question exceeds {config.MAX_INPUT_CHARS} characters")
    return text


class Policy:
    """Decides whether a tool call may run; write tools need human approval."""

    def __init__(
        self, allowed: set[str], writes: set[str], ask: Callable[[str], str] = input
    ):
        """Store the allowlist, the write-tool set and the approval callable."""
        self.allowed, self.writes, self.ask = allowed, writes, ask

    def authorize(self, name: str, args: dict) -> tuple[bool, str]:
        """Return (ok, reason); reason is the denial text when not ok."""
        # Allowlist first: anything unknown is denied without bothering the human.
        if name not in self.allowed:
            return False, "Tool not allowed by policy"
        # Writes pause for explicit approval; only y/Y counts as yes.
        if name in self.writes:
            print(f"\nProposed write tool: {name}\n{json.dumps(args, indent=2)}")
            if self.ask("Approve? [y/N] ") not in ("y", "Y"):
                return False, "Denied by human reviewer"
        return True, ""


def shape_output(name: str, text: str) -> str:
    """Pass-through hook reserved for field shaping (token optimization)."""
    return text


def wrap_output(name: str, text: str) -> str:
    """Sanitize, optionally truncate, and wrap tool output as untrusted data."""
    text = shape_output(name, text)
    # Remove any tool_output tags so data can't close or forge the wrapper.
    text = re.sub(r"</?tool_output[^>]*>", "", text, flags=re.IGNORECASE)
    # Truncation is the token-optimization switch (TOKEN_OPTIMIZATION in .env).
    limit = config.MAX_TOOL_OUTPUT_CHARS
    if config.TOKEN_OPTIMIZATION and len(text) > limit:
        text = f"{text[:limit]}[truncated {len(text) - limit} chars]"
    return f'<tool_output tool="{name}">{text}</tool_output>'
