"""The Textual TUI: a process sidebar plus a live log pane, with start/stop/restart controls."""

from __future__ import annotations

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Label, ListItem, ListView, RichLog

from devp.messages import LogLine, ProcessError, ProcessStateChanged
from devp.process import ProcessManager, ProcessState
from devp.widgets import status_label


class ProcessListItem(ListItem):
    """A sidebar row that remembers which process it represents."""

    def __init__(self, process_name: str, state: ProcessState) -> None:
        super().__init__(Label(status_label(process_name, state)))
        self.process_name = process_name


class DevpApp(App[None]):
    """Main devp application: process sidebar, log pane, and start/stop/restart controls."""

    CSS = """
    #sidebar {
        width: 32;
        border-right: solid $panel;
    }
    #log {
        width: 1fr;
    }
    """

    BINDINGS = [
        ("s", "start_selected", "Start"),
        ("x", "stop_selected", "Stop"),
        ("r", "restart_selected", "Restart"),
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, manager: ProcessManager) -> None:
        super().__init__()
        self.manager = manager
        self.manager.build(
            on_output=self._on_output,
            on_state_change=self._on_state_change,
            on_error=self._on_error,
        )
        self._process_names = list(manager.processes.keys())
        self.selected_name: str | None = self._process_names[0] if self._process_names else None
        self._list_items: dict[str, ProcessListItem] = {}

    def compose(self) -> ComposeResult:
        """Lay out the sidebar (process list) and the log pane side by side."""
        yield Header()
        with Horizontal():
            items = []
            for name in self._process_names:
                item = ProcessListItem(name, self.manager.processes[name].state)
                self._list_items[name] = item
                items.append(item)
            yield ListView(*items, id="sidebar")
            yield RichLog(id="log", wrap=True, markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        """Focus the sidebar and autostart any processes configured for it."""
        self.query_one("#sidebar", ListView).focus()
        self._refresh_log_pane()
        await self.manager.autostart()

    def _on_output(self, process_name: str, line: str) -> None:
        """Forward a process's output line into the app's message queue (thread-safe hop)."""
        self.post_message(LogLine(process_name, line))

    def _on_state_change(self, process_name: str, state: ProcessState) -> None:
        """Forward a process's state change into the app's message queue."""
        self.post_message(ProcessStateChanged(process_name, state))

    def _on_error(self, process_name: str, text: str) -> None:
        """Forward a process error into the app's message queue."""
        self.post_message(ProcessError(process_name, text))

    def on_log_line(self, message: LogLine) -> None:
        """Append the line to the log pane if it belongs to the currently selected process."""
        if message.process_name == self.selected_name:
            self.query_one("#log", RichLog).write(message.line)

    def on_process_state_changed(self, message: ProcessStateChanged) -> None:
        """Refresh the sidebar glyph and show a brief toast for the new state."""
        item = self._list_items.get(message.process_name)
        if item is not None:
            item.query_one(Label).update(status_label(message.process_name, message.state))

        name = escape(message.process_name)
        if message.state == ProcessState.RUNNING:
            self.notify(f"'{name}' started", severity="information", timeout=3)
        elif message.state == ProcessState.STOPPED:
            self.notify(f"'{name}' stopped", severity="information", timeout=3)

    def on_process_error(self, message: ProcessError) -> None:
        """Show a process failure (bad command, crash) as an error toast."""
        self.notify(
            escape(message.text),
            title=escape(message.process_name),
            severity="error",
            timeout=8,
        )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Switch the log pane to whichever process is now highlighted in the sidebar."""
        item = event.item
        if isinstance(item, ProcessListItem):
            self.selected_name = item.process_name
            self._refresh_log_pane()

    def _refresh_log_pane(self) -> None:
        """Clear the log pane and replay the selected process's buffered output into it."""
        log = self.query_one("#log", RichLog)
        log.clear()
        if self.selected_name is None:
            return
        process = self.manager.processes[self.selected_name]
        for line in process.output:
            log.write(line)

    async def action_start_selected(self) -> None:
        """Start the currently selected process (bound to 's')."""
        process = self._selected_process()
        if process is not None:
            await process.start()

    async def action_stop_selected(self) -> None:
        """Stop the currently selected process (bound to 'x')."""
        process = self._selected_process()
        if process is not None:
            await process.stop()

    async def action_restart_selected(self) -> None:
        """Restart the currently selected process (bound to 'r')."""
        process = self._selected_process()
        if process is not None:
            await process.restart()

    def _selected_process(self):
        """Return the `ManagedProcess` for the sidebar selection, if any."""
        if self.selected_name is None:
            return None
        return self.manager.processes.get(self.selected_name)

    async def action_quit(self) -> None:
        """Stop every managed process before exiting, so none are left orphaned."""
        await self.manager.shutdown_all()
        self.exit()
