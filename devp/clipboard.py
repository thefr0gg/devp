"""Copying text to the system clipboard via whatever native tool is available."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def _native_command() -> tuple[list[str], str] | None:
    """The clipboard command for this platform and the encoding it expects, if any."""
    if sys.platform == "win32":
        # clip.exe reads UTF-16 when the input starts with a byte-order mark.
        return (["clip"], "utf-16") if shutil.which("clip") else None
    if sys.platform == "darwin":
        return (["pbcopy"], "utf-8") if shutil.which("pbcopy") else None
    candidates = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(["wl-copy"])
    if os.environ.get("DISPLAY"):
        candidates += [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for command in candidates:
        if shutil.which(command[0]):
            return command, "utf-8"
    return None


def copy_native(text: str) -> bool:
    """Copy `text` using the platform's clipboard tool; returns whether it succeeded.

    Complements Textual's OSC 52 copy, which only works in terminals that support it
    (e.g. not macOS Terminal or the classic Windows console).
    """
    found = _native_command()
    if found is None:
        return False
    command, encoding = found
    try:
        subprocess.run(
            command,
            input=text.encode(encoding),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True
