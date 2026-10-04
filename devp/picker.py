"""An arrow-key menu for `devp init`, drawn in place in the terminal (no full-screen UI)."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

_IRIS, _FOAM, _MUTED = "#c4a7e7", "#9ccfd8", "#908caa"


def decode_escape(sequence: str) -> str:
    """The key for what follows an ESC byte: an arrow, or a lone Escape if it's anything else."""
    return {"[A": "up", "[B": "down", "OA": "up", "OB": "down"}.get(sequence, "escape")


def _read_key_windows() -> str:
    import msvcrt

    char = msvcrt.getwch()
    if char in ("\x00", "\xe0"):  # the first half of an arrow/function key
        return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
    return _plain_key(char)


def _read_key_posix() -> str:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)  # keys arrive immediately and unechoed; Ctrl+C still interrupts
        char = os.read(fd, 1).decode(errors="ignore")
        if char != "\x1b":
            return _plain_key(char)
        sequence = ""
        while len(sequence) < 2 and select.select([fd], [], [], 0.05)[0]:
            sequence += os.read(fd, 1).decode(errors="ignore")
        return decode_escape(sequence)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _plain_key(char: str) -> str:
    if char in ("\r", "\n"):
        return "enter"
    if char == "\x03":
        return "ctrl-c"
    if char == "\x1b":
        return "escape"
    return char


def read_key() -> str:
    """Block for one key press: 'up', 'down', 'enter', 'escape', 'ctrl-c', or the character."""
    return _read_key_windows() if sys.platform == "win32" else _read_key_posix()


def _render(templates: dict[str, str], index: int, fancy: bool) -> Group:
    pointer = "›" if fancy else ">"
    width = max(len(name) for name in templates)
    lines = [Text("Choose a template for devp.toml", style="bold"), Text("")]
    for i, (name, description) in enumerate(templates.items()):
        line = Text("  ")
        if i == index:
            line.append(f"{pointer} ", style=f"bold {_IRIS}")
            line.append(f"{name:<{width}}", style=f"bold {_FOAM}")
            line.append(f"  {description}")
        else:
            line.append("  ")
            line.append(f"{name:<{width}}")
            line.append(f"  {description}", style=_MUTED)
        lines.append(line)
    hint = "↑/↓ to move · enter to select · esc to cancel"
    if not fancy:
        hint = "up/down to move - enter to select - esc to cancel"
    lines += [Text(""), Text(hint, style=_MUTED)]
    return Group(*lines)


def choose_template(
    templates: dict[str, str],
    suggested: str | None = None,
    *,
    key_source: Callable[[], str] = read_key,
    console: Console | None = None,
) -> str | None:
    """Let the user pick a template with the arrow keys; None if they cancel.

    The menu is drawn in place and erased once a choice is made. `suggested` starts
    highlighted. Navigation wraps around; `j`/`k` also move.
    """
    console = console or Console()
    names = list(templates)
    index = names.index(suggested) if suggested in templates else 0
    fancy = _encodes(console.encoding, "›↑↓·")  # else plain ASCII, which any console can show

    with Live(
        _render(templates, index, fancy),
        console=console,
        transient=True,
        auto_refresh=False,
    ) as live:
        while True:
            try:
                key = key_source()
            except KeyboardInterrupt:
                return None
            if key in ("up", "k"):
                index = (index - 1) % len(names)
            elif key in ("down", "j"):
                index = (index + 1) % len(names)
            elif key == "enter":
                return names[index]
            elif key in ("escape", "ctrl-c", "q"):
                return None
            live.update(_render(templates, index, fancy), refresh=True)


def _encodes(encoding: str | None, text: str) -> bool:
    try:
        text.encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True
