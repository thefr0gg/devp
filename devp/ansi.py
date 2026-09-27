"""Stripping terminal escape sequences from process output."""

from __future__ import annotations

import re

# ANSI escape sequences (colors, cursor movement, window titles) that processes print.
_sub_ansi = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])").sub


def strip_ansi(line: str) -> str:
    """Remove ANSI escape sequences from `line`."""
    return _sub_ansi("", line) if "\x1b" in line else line
