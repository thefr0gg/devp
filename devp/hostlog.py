"""The terminal output of `devp --web`: a short banner, then one tidy line per event."""

from __future__ import annotations

from datetime import datetime

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devp import __version__
from devp.process import ProcessState

# The Rosé Pine colors the TUI uses, so the host's terminal and the web UI feel related.
_FOAM, _PINE, _LOVE, _GOLD, _IRIS, _MUTED = "#9ccfd8", "#31748f", "#eb6f92", "#f6c177", "#c4a7e7", "#908caa"

_STATE_LINES: dict[ProcessState, tuple[str, str, str]] = {
    ProcessState.STARTING: ("▶", _GOLD, "starting"),
    ProcessState.RUNNING: ("●", _FOAM, "running"),
    ProcessState.STOPPED: ("■", _MUTED, "stopped"),
    ProcessState.SCHEDULED: ("◷", _IRIS, "scheduled"),
}


# For a terminal (or redirected output) whose encoding can't show the glyphs above.
_ASCII = str.maketrans({"●": "*", "○": "o", "▶": ">", "■": "#", "◷": "~", "↻": "@", "✗": "x", "·": "-"})


def _can_encode(encoding: str | None) -> bool:
    try:
        "●○▶■◷↻✗·".encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


class HostLog:
    """Prints the banner and event lines for a running `devp --web`."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(highlight=False)
        self._ascii = not _can_encode(self.console.encoding)

    def _fit(self, text: str) -> str:
        return text.translate(_ASCII) if self._ascii else text

    def banner(self, url: str, config_name: str, process_count: int, api_port: int | None) -> None:
        rows = [
            ("open", f"[bold {_FOAM}]{escape(url)}[/]"),
            ("config", f"{escape(config_name)} [{_MUTED}]·[/] {_count(process_count)}"),
            ("api", f"127.0.0.1:{api_port}" if api_port else "off"),
        ]
        title = f"\n  [bold {_IRIS}]devp[/] [{_MUTED}]web · v{__version__}[/]\n"
        self.console.print(self._fit(title))
        for label, value in rows:
            self.console.print(self._fit(f"  [{_MUTED}]{label:<7}[/]{value}"))
        self.console.print(f"\n  [{_MUTED}]Ctrl+C stops everything and quits[/]\n")

    def event(self, glyph: str, color: str, label: str, message: str, *more: str) -> None:
        """One line: time, a colored glyph, a fixed-width label, then the message.

        A grid, so a long message wraps under itself instead of under the time. Any
        `more` lines are continuation rows in the message column.
        """
        now = datetime.now().strftime("%H:%M:%S")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=10, no_wrap=True)  # indent + time
        grid.add_column(width=1)
        grid.add_column(width=10, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(
            f"  [{_MUTED}]{now}[/]",
            f"[{color}]{self._fit(glyph)}[/]",
            f"[{color}]{escape(label)}[/]",
            self._fit(message),
        )
        for line in more:
            grid.add_row("", "", "", self._fit(f"[{_MUTED}]{line}[/]"))
        self.console.print(grid)

    def session(self, peer: str, joined: bool, seconds: float = 0) -> None:
        if joined:
            self.event("●", _FOAM, "session", f"{escape(peer)} [{_MUTED}]connected[/]")
        else:
            self.event("○", _MUTED, "session", f"{escape(peer)} [{_MUTED}]left after {_span(seconds)}[/]")

    def blocked(self, peer: str) -> None:
        self.event("!", _GOLD, "auth", f"blocked a request from {escape(peer)} [{_MUTED}](missing or wrong token)[/]")

    def process_state(self, name: str, state: ProcessState) -> None:
        line = _STATE_LINES.get(state)
        if line is not None:
            glyph, color, word = line
            self.event(glyph, color, name, f"[{color}]{word}[/]")

    def process_error(self, name: str, text: str) -> None:
        self.event("✗", _LOVE, name, f"[{_LOVE}]{escape(text)}[/]")

    def reload_started(self, config_name: str, stopping: int) -> None:
        self.event("↻", _IRIS, "config", f"{escape(config_name)} changed [{_MUTED}]· stopping {_count(stopping)}[/]")

    def reload_finished(self, starting: int) -> None:
        self.event("↻", _IRIS, "config", f"reloaded [{_MUTED}]· starting {_count(starting)}[/]")

    def config_error(self, config_name: str, error: str) -> None:
        self.event(
            "✗",
            _LOVE,
            "config",
            f"{escape(config_name)} has an error; keeping the previous config",
            *(escape(line) for line in error.splitlines()),
        )

    def warning(self, text: str) -> None:
        self.event("!", _GOLD, "config", escape(text))

    def shutting_down(self) -> None:
        self.event("■", _MUTED, "shutdown", "stopping everything")


def _count(n: int) -> str:
    return f"{n} process" + ("" if n == 1 else "es")


def _span(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"
