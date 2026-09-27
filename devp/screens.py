"""Modal screens layered over the main devp UI."""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


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
        self._running = running

    def compose(self) -> ComposeResult:
        with Vertical(id="reload-dialog") as dialog:
            dialog.border_title = "Config changed"
            yield Label(
                f"[b]{escape(self._config_name)}[/b] was modified. Reload it now?"
            )
            if self._running:
                names = ", ".join(escape(name) for name in self._running)
                yield Label(
                    f"This will stop {len(self._running)} running process(es): {names}",
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
