"""Choosing the shell that runs a process's string `command`."""

from __future__ import annotations

import shutil
import sys
from pathlib import PurePath

_IS_WINDOWS = sys.platform == "win32"

# How each known shell takes a command string, keyed by executable name (no ".exe").
# Any other shell is assumed to follow the POSIX convention: `<shell> -c <command>`.
_SHELL_FLAGS: dict[str, list[str]] = {
    "pwsh": ["-NoLogo", "-NoProfile", "-Command"],
    "powershell": ["-NoLogo", "-NoProfile", "-Command"],
    "cmd": ["/d", "/s", "/c"],
}
_POSIX_FLAGS = ["-c"]

Shell = str | list[str]


class ShellNotFoundError(OSError):
    """The configured shell isn't installed (or isn't on PATH)."""


def _shell_name(executable: str) -> str:
    name = PurePath(executable.replace("\\", "/")).name.lower()
    return name.removesuffix(".exe")


def shell_argv(shell: Shell | None, command: str) -> list[str] | None:
    """The argv that runs `command` in `shell`, or None to use the system's default shell.

    `shell` is either a shell's name or path (flags are picked for known shells), or a
    list: an exact argv prefix that `command` is appended to, for full control.
    """
    if shell is None:
        return None
    if isinstance(shell, list):
        executable, *flags = shell
    else:
        executable = shell
        flags = _SHELL_FLAGS.get(_shell_name(shell), _POSIX_FLAGS)
        # On Windows, cmd needs its own command-line quoting, which the system default
        # shell (COMSPEC, normally cmd.exe) already applies.
        if _IS_WINDOWS and _shell_name(shell) == "cmd":
            return None

    resolved = shutil.which(executable)
    if resolved is None:
        raise ShellNotFoundError(f"shell '{executable}' not found")
    return [resolved, *flags, command]

