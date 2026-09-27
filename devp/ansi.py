"""Turning raw terminal output into plain lines: escape codes, redraws, and backspaces."""

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


# Escape sequences that return to the start of the line, like "\r": cursor to column 1
# (CSI G / 0G / 1G) and erase-entire-line (CSI 2K). Spinners and progress bars use them.
_line_restart = re.compile(r"\x1b\[(?:[01]?G|2K)")


def resolve_overwrites(line: str) -> str:
    """Collapse in-place redraws into what a terminal would end up showing.

    Progress bars and spinners redraw one line by writing "\\r" (or an escape that
    returns to column 1) followed by the new text, so a single line can hold dozens
    of intermediate frames. Keep the last frame that has visible text, and apply
    backspaces, so the log shows e.g. "[100%] done" instead of every frame glued
    together with the carriage returns in between.
    """
    if "\x1b" in line:
        line = _line_restart.sub("\r", line)
    if "\r" in line:
        frames = line.split("\r")
        line = next((f for f in reversed(frames) if strip_ansi(f).strip()), frames[-1])
    if "\b" in line:
        kept: list[str] = []
        for char in line:
            if char == "\b":
                if kept:
                    kept.pop()
            else:
                kept.append(char)
        line = "".join(kept)
    return line
