"""Policy layer: input check, tool allowlist, write approval, untrusted-output wrapping."""

import json
import re
from typing import Callable

import config
from errors import InputError


def check_input(text: str) -> str:
    """Strip the prompt; reject empty or oversized input."""
    text = text.strip()
    if not text:
        raise InputError("Prompt is empty")
    if len(text) > config.MAX_INPUT_CHARS:
        raise InputError(f"Prompt exceeds {config.MAX_INPUT_CHARS} characters")
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
        if name not in self.allowed:
            return False, "Tool not allowed by policy"
        if name in self.writes:
            print(f"\nProposed write tool: {name}\n{json.dumps(args, indent=2)}")
            # Fail closed: if the prompt itself breaks (no stdin, broken terminal), the write is denied.
            try:
                answer = self.ask("Approve? [y/N] ")
            except Exception as e:
                return False, f"Denied: approval prompt failed ({type(e).__name__})"
            if answer not in ("y", "Y"):
                return False, "Denied by human reviewer"
        return True, ""


_WRAPPER_TAG = re.compile(r"</?tool_output[^>]*>", re.IGNORECASE)


def shape_output(name: str, text: str) -> str:
    """Pass-through hook reserved for field shaping (token optimization)."""
    return text


def wrap_output(name: str, text: str) -> str:
    """Sanitize, optionally truncate, and wrap tool output as untrusted data."""
    text = shape_output(name, text)
    # Strip wrapper tags until none remain: one pass can leave a new tag behind
    # ("</tool_<tool_output>output>" becomes "</tool_output>").
    while (cleaned := _WRAPPER_TAG.sub("", text)) != text:
        text = cleaned
    limit = config.MAX_TOOL_OUTPUT_CHARS
    if config.TOKEN_OPTIMIZATION and len(text) > limit:
        text = f"{text[:limit]}[truncated {len(text) - limit} chars]"
    # The name comes from the model; fullmatch (unlike "$") also rejects a trailing newline.
    label = name if re.fullmatch(r"[A-Za-z0-9_]+", name) else "unknown"
    return f'<tool_output tool="{label}">{text}</tool_output>'
