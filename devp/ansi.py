"""Stripping terminal escape sequences from process output."""

from __future__ import annotations

import re

# ANSI escape sequences (colors, cursor movement, window titles) that processes print.
_ansi_pattern = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
_sub_ansi = _ansi_pattern.sub


def strip_ansi(line: str) -> str:
    """Remove ANSI escape sequences from `line`."""
    return _sub_ansi("", line) if "\x1b" in line else line


def _keep_sgr(match: re.Match[str]) -> str:
    sequence = match.group(0)
    return sequence if sequence.startswith("\x1b[") and sequence.endswith("m") else ""


def only_sgr(line: str) -> str:
    """Remove every escape sequence except SGR (colors and text styles)."""
    return _ansi_pattern.sub(_keep_sgr, line) if "\x1b" in line else line
