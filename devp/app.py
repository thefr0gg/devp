"""The Textual TUI: a process sidebar plus a live log pane, with start/stop/restart controls."""

from __future__ import annotations

from datetime import datetime

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Footer, Input, Label, ListItem, ListView

from devp.cron import CronJob
from devp.log_view import LogView
from devp.manager import ProcessManager
from devp.messages import ProcessError, ProcessStateChanged
from devp.process import MAX_BUFFER_LINES, ProcessState
from devp.widgets import format_duration, status_label

_CRON_TICK_INTERVAL = 1.0
# Output is buffered and written to the log pane in batches at most this often, so a
# chatty process costs one render per frame instead of one message + render per line.
_LOG_FLUSH_INTERVAL = 1 / 30


def _status_detail(runnable: object) -> str | None:
    """Show a live countdown to a cron job's next scheduled run, when it's idle and enabled."""
    if (
        isinstance(runnable, CronJob)
        and runnable.state == ProcessState.SCHEDULED
        and runnable.next_run_at is not None
    ):
        remaining = (runnable.next_run_at - datetime.now()).total_seconds()
        return f"next in {format_duration(remaining)}"
    return None


class ProcessListItem(ListItem):
    """A sidebar row that remembers which process it represents."""

    def __init__(self, process_name: str, state: ProcessState, detail: str | None = None) -> None:
        self.label_text = status_label(process_name, state, detail)
        self.label = Label(self.label_text)
        super().__init__(self.label)
        self.process_name = process_name

    def set_status(self, state: ProcessState, detail: str | None = None) -> None:
        """Update the row's label, skipping the refresh when nothing visible changed."""
        text = status_label(self.process_name, state, detail)
        if text != self.label_text:
            self.label_text = text
            self.label.update(text)


class DevpApp(App[None]):
    """Main devp application: process sidebar, log pane, and start/stop/restart controls."""

    CSS = """
    Screen {
        background: transparent;
    }
    Footer {
        background: transparent;
    }
    #sidebar, #log-pane {
        background: transparent;
        border: round $panel;
        border-title-color: $text-muted;
    }
    #sidebar {
        width: 32;
    }
    #log-pane {
        width: 1fr;
    }
    /* Highlight whichever pane has keyboard focus. */
    #sidebar:focus, #log-pane:focus-within {
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
    }
    ListView, ListItem {
        background: transparent;
    }
    ListView > ListItem.-highlight {
        background: $panel;
    }
    ListView:focus > ListItem.-highlight {
        background: $accent 40%;
        text-style: bold;
    }
    #log {
        height: 1fr;
        background: transparent;
    }
    /* Notifications: compact rounded cards matching the panes, tinted by severity. */
    ToastRack {
        margin: 0 2 1 0;
    }
    Toast {
        width: 44;
        max-width: 60%;
        margin-top: 0;
        padding: 0 1;
        background: $surface;
        border: round $panel;
    }
    Toast.-information {
        border: round $success;
    }
    Toast.-warning {
        border: round $warning;
    }
    Toast.-error {
        border: round $error;
    }
    #search-input {
        height: 3;
        border: round $accent;
        background: transparent;
    }
    """

    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        ("s", "start_selected", "Run"),
        ("x", "stop_selected", "Stop"),
        ("r", "restart_selected", "Restart"),
        ("slash", "search", "Search"),
        ("n", "next_match", "Next match"),
        ("N", "prev_match", "Prev match"),
        ("escape", "close_search", "Close search"),
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
        self._search_query = ""
        self._search_cursor: int | None = None  # log line index of the current match
        self._pre_search_focus = None
        self._pending_lines: list[str] = []
        self._flush_timer: Timer | None = None
        self._log_view = LogView(max_lines=MAX_BUFFER_LINES, id="log")

    def compose(self) -> ComposeResult:
        """Lay out the sidebar (process list) and the log pane side by side."""
        with Horizontal():
            items = []
            for name in self._process_names:
                runnable = self.manager.processes[name]
                item = ProcessListItem(name, runnable.state, detail=_status_detail(runnable))
                self._list_items[name] = item
                items.append(item)
            yield ListView(*items, id="sidebar")
            with Vertical(id="log-pane"):
                yield self._log_view
                yield Input(placeholder="› search logs", id="search-input")
        yield Footer()

    async def on_mount(self) -> None:
        """Focus the sidebar, hide the search bar, and autostart configured processes."""
        self.query_one("#search-input", Input).display = False
        sidebar = self.query_one("#sidebar", ListView)
        sidebar.border_title = "Processes"
        sidebar.focus()
        self._refresh_log_pane()
        self.set_interval(_CRON_TICK_INTERVAL, self._tick_cron_labels)
        await self.manager.autostart()

    def _tick_cron_labels(self) -> None:
        """Refresh every cron job's sidebar label so its 'next run' countdown ticks live."""
        for name, item in self._list_items.items():
            runnable = self.manager.processes[name]
            if isinstance(runnable, CronJob):
                item.set_status(runnable.state, _status_detail(runnable))

    def _on_output(self, process_name: str, line: str) -> None:
        """Queue a line of the selected process's output for the next batched log write.

        Other processes' output needs no UI work: it's already in their scrollback
        buffer and gets replayed when they're selected.
        """
        if process_name != self.selected_name:
            return
        self._pending_lines.append(line)
        if self._flush_timer is None:
            self._flush_timer = self.set_timer(_LOG_FLUSH_INTERVAL, self._flush_pending_lines)

    def _flush_pending_lines(self) -> None:
        """Write all queued output lines to the log pane in one go."""
        self._flush_timer = None
        lines, self._pending_lines = self._pending_lines, []
        if self._log_view.is_attached:  # the timer can fire during shutdown
            self._log_view.write_lines(lines)

    def _discard_pending_lines(self) -> None:
        """Drop queued lines (e.g. before a full replay, which already includes them)."""
        if self._flush_timer is not None:
            self._flush_timer.stop()
            self._flush_timer = None
        self._pending_lines = []

    def _on_state_change(self, process_name: str, state: ProcessState) -> None:
        """Forward a process's state change into the app's message queue."""
        self.post_message(ProcessStateChanged(process_name, state))

    def _on_error(self, process_name: str, text: str) -> None:
        """Forward a process error into the app's message queue."""
        self.post_message(ProcessError(process_name, text))

    def on_process_state_changed(self, message: ProcessStateChanged) -> None:
        """Refresh the sidebar glyph and show a brief toast for the new state."""
        item = self._list_items.get(message.process_name)
        if item is not None:
            runnable = self.manager.processes.get(message.process_name)
            detail = _status_detail(runnable) if runnable is not None else None
            item.set_status(message.state, detail)

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
            self._search_query = ""
            self._refresh_log_pane()

    def _refresh_log_pane(self) -> None:
        """Clear the log pane and replay the selected process's buffered output into it."""
        self.query_one("#log-pane").border_title = (
            f"Logs · {escape(self.selected_name)}" if self.selected_name else "Logs"
        )
        self._discard_pending_lines()
        log = self._log_view
        log.clear()
        log.set_highlight(self._search_query)
        self._search_cursor = None
        if self.selected_name is not None:
            log.write_lines(self.manager.processes[self.selected_name].output)

    def action_search(self) -> None:
        """Open the search bar for the currently selected process's log (bound to '/')."""
        if self.selected_name is None:
            return
        self._pre_search_focus = self.focused
        search_input = self.query_one("#search-input", Input)
        search_input.display = True
        search_input.focus()

    def _close_search_input(self, *, focus_log: bool = False) -> None:
        """Hide the search bar, clear its text, and restore keyboard focus somewhere sane.

        Without restoring focus, the hidden Input keeps it and silently swallows every
        keystroke typed afterwards — they don't reach the sidebar or log pane, and
        reappear as stale leftover text if the search bar is reopened. A successful
        search moves focus to the log pane (so arrow keys / n / N can browse matches
        right away); cancelling restores whatever had focus before search was opened.
        """
        search_input = self.query_one("#search-input", Input)
        search_input.display = False
        search_input.value = ""
        target = self._pre_search_focus
        self._pre_search_focus = None
        if focus_log:
            self._log_view.focus()
        elif target is not None:
            target.focus()
        else:
            self.query_one("#sidebar", ListView).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Run the search typed into the search bar and jump to the first match."""
        if event.input.id != "search-input":
            return

        query = event.value.strip()
        self._close_search_input(focus_log=True)

        self._search_query = query
        self._search_cursor = None
        log = self._log_view
        log.set_highlight(query)

        if not query:
            return

        matches = log.find(query)
        if matches:
            self._jump_to_match(matches[0])
            self.notify(
                f"{len(matches)} match(es) for '{escape(query)}'",
                severity="information",
                timeout=3,
            )
        else:
            self.notify(f"No matches for '{escape(query)}'", severity="warning", timeout=3)

    def action_next_match(self) -> None:
        """Jump to the next search match (bound to 'n'), wrapping around at the end."""
        matches = self._log_view.find(self._search_query)
        if not matches:
            return
        cursor = self._search_cursor
        later = [m for m in matches if cursor is None or m > cursor]
        self._jump_to_match(later[0] if later else matches[0])

    def action_prev_match(self) -> None:
        """Jump to the previous search match (bound to 'N'), wrapping around at the start."""
        matches = self._log_view.find(self._search_query)
        if not matches:
            return
        cursor = self._search_cursor
        earlier = [m for m in matches if cursor is None or m < cursor]
        self._jump_to_match(earlier[-1] if earlier else matches[-1])

    def _jump_to_match(self, index: int) -> None:
        self._search_cursor = index
        self._log_view.scroll_to_line(index)

    def action_close_search(self) -> None:
        """Close the search bar and clear any active search highlighting (bound to Escape)."""
        search_input = self.query_one("#search-input", Input)
        if not search_input.display:
            return
        self._close_search_input()
        if self._search_query:
            self._search_query = ""
            self._search_cursor = None
            self._log_view.set_highlight("")

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
