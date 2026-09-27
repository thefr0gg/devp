"""Modal screens layered over the main devp UI."""

from __future__ import annotations

from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ReloadConfigScreen(ModalScreen[bool]):
    """Ask whether to stop everything and reload a changed devp.toml.

    Dismisses with True to reload, False to keep running the current config.
    """

    DEFAULT_CSS = """
    ReloadConfigScreen {
        align: center middle;
        background: $background 60%;
    }
    #reload-dialog {
        width: 60;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
    }
    #reload-dialog Label {
        width: 100%;
    }
    #reload-running {
        margin-top: 1;
        color: $text-muted;
    }
    #reload-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }
    #reload-buttons Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Reload"),
        Binding("n,escape", "cancel", "Keep current"),
    ]

    def __init__(self, config_name: str, running: list[str]) -> None:
        super().__init__()
        self._config_name = config_name
        self._running_names = running

    def compose(self) -> ComposeResult:
        with Vertical(id="reload-dialog") as dialog:
            dialog.border_title = "Config changed"
            yield Label(
                f"[b]{escape(self._config_name)}[/b] was modified. Reload it now?"
            )
            if self._running_names:
                names = ", ".join(escape(name) for name in self._running_names)
                yield Label(
                    f"This will stop {len(self._running_names)} running process(es): {names}",
                    id="reload-running",
                )
            else:
                yield Label("No processes are currently running.", id="reload-running")
            with Horizontal(id="reload-buttons"):
                yield Button("Keep current (n)", id="reload-cancel")
                yield Button("Stop & reload (y)", id="reload-confirm", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#reload-confirm", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "reload-confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# Grouped by where each key works; the footer shows the same keys per focused pane.
HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Process list",
        [
            ("↑ / ↓", "Select a process"),
            ("s", "Run the selected process (a cron job runs now)"),
            ("x", "Stop the selected process"),
            ("r", "Restart the selected process"),
            ("S / X", "Start / stop everything, in depends_on order"),
            ("/", "Search the selected process's log"),
            ("double-click", "Run the clicked process"),
        ],
    ),
    (
        "Log pane",
        [
            ("PgUp / PgDn", "Scroll (Home / End jump to the top / bottom)"),
            ("G", "Jump to the newest output and follow it"),
            ("c", "Clear this log"),
            ("/", "Search this log"),
            ("n / N", "Next / previous match (after a search)"),
            ("Esc", "Clear the search highlighting"),
            ("drag", "Select text with the mouse"),
            ("Ctrl+C", "Copy the selection (quits when nothing is selected)"),
        ],
    ),
    (
        "Search bar",
        [
            ("Enter", "Search and jump to the first match"),
            ("Esc", "Cancel"),
        ],
    ),
    (
        "Anywhere",
        [
            ("Tab", "Switch between the process list and the log pane"),
            ("?", "Show or hide this help"),
            ("q", "Quit (stops every process first)"),
        ],
    ),
]


class HelpScreen(ModalScreen[None]):
    """Every key and mouse action, grouped by what it acts on."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-dialog {
        width: 68;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        padding: 0 2;
        background: $surface;
        color: $foreground;
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
        border-subtitle-color: $text-muted;
    }
    #help-dialog Static {
        width: 100%;
    }
    """

    BINDINGS = [Binding("escape,question_mark,q", "close", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-dialog") as dialog:
            dialog.border_title = "✦ devp keys ✦"
            dialog.border_subtitle = "? / Esc to close"
            yield Static(self._render_help())

    def _render_help(self) -> Table:
        accent = f"bold {self.app.theme_variables['accent']}"
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold", no_wrap=True)
        table.add_column()
        for index, (section, keys) in enumerate(HELP_SECTIONS):
            if index:
                table.add_row("", "")
            table.add_row(Text(section, style=accent), "")
            for key, description in keys:
                table.add_row(Text(key, style="bold"), description)
        return table

    def action_close(self) -> None:
        self.dismiss()
