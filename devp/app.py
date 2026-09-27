"""The Textual TUI: a process sidebar plus a live log pane, with start/stop/restart controls."""

from __future__ import annotations

from datetime import datetime
from functools import partial

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.events import Click, Resize
from textual.message import Message
from textual.notifications import Notify
from textual.timer import Timer
from textual.widgets import Footer, Input, Label, ListItem, ListView
from textual.worker import Worker

from devp.clipboard import copy_native
from devp.cron import CronJob
from devp.log_view import LogView
from devp.manager import ProcessManager
from devp.messages import ProcessError, ProcessStateChanged
from devp.process import MAX_BUFFER_LINES, ProcessState
from devp.screens import HelpScreen
from devp.widgets import format_duration, is_animated, status_label

# How often animated sidebar glyphs advance a frame (also ticks cron countdowns).
_GLYPH_FRAME_INTERVAL = 0.1
# Output is buffered and written to the log pane in batches at most this often, so a
# chatty process costs one render per frame instead of one message + render per line.
_LOG_FLUSH_INTERVAL = 1 / 30
# Rows the toast stack must leave free (pane top border, plus the rack's bottom margin
# with the search bar open), and the tallest a typical toast gets (border, title, text).
_TOAST_RESERVED_ROWS = 6
_TOAST_ROWS = 4


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


def _log_title(name: str | None, runnable: object | None) -> str:
    """The log pane title: the process name plus live details, e.g.
    `Logs · api · pid 4312 · up 5m12s · restarts 2`, or `Logs · api · exit 1`."""
    if name is None or runnable is None:
        return "Logs"
    details = []
    pid = getattr(runnable, "pid", None)
    if pid is not None:
        details += [f"pid {pid}", f"up {format_duration(runnable.uptime or 0)}"]
        restarts = runnable.start_count - 1
        if restarts > 0 and not isinstance(runnable, CronJob):
            details.append(f"restarts {restarts}")
    elif runnable.exit_code is not None:
        details.append(f"exit {runnable.exit_code}")
    title = f"Logs · {escape(name)}"
    return title + "".join(f" [dim]· {detail}[/dim]" for detail in details)


def _osc_color(color: str) -> str:
    """A color as `rgb:RR/GG/BB`, the format every OSC 10/11 terminal accepts."""
    r, g, b = Color.parse(color).rgb
    return f"rgb:{r:02x}/{g:02x}/{b:02x}"


class ProcessListItem(ListItem):
    """A sidebar row that remembers which process it represents."""

    class DoubleClicked(Message):
        """The row was double-clicked (the first click already selected it)."""

        def __init__(self, process_name: str) -> None:
            super().__init__()
            self.process_name = process_name

    def __init__(self, process_name: str, state: ProcessState, detail: str | None = None) -> None:
        self.label_text = status_label(process_name, state, detail)
        self.label = Label(self.label_text)
        super().__init__(self.label)
        self.process_name = process_name

    def set_status(self, state: ProcessState, detail: str | None = None, frame: int = 0) -> None:
        """Update the row's label, skipping the refresh when nothing visible changed."""
        text = status_label(self.process_name, state, detail, frame)
        if text != self.label_text:
            self.label_text = text
            self.label.update(text)

    def on_click(self, event: Click) -> None:
        if event.chain == 2:
            self.post_message(self.DoubleClicked(self.process_name))


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
    /* Highlighted rows paint their own background, so they pair it with the theme's
       text color rather than the terminal default (which could be dark on dark). */
    ListView > ListItem.-highlight {
        background: $panel;
        color: $foreground;
    }
    ListView:focus > ListItem.-highlight {
        background: $accent 40%;
        color: $foreground;
        text-style: bold;
    }
    #log {
        height: 1fr;
        background: transparent;
        /* Blend the track into the pane instead of a solid black strip. */
        scrollbar-background: transparent;
        scrollbar-background-hover: transparent;
        scrollbar-background-active: transparent;
    }
    /* Notifications: rounded outlines like the panes, colored by severity, stacked in
       the bottom-right of the log pane. The fill is transparent because a box-drawing
       line sits mid-cell, so any fill color would spill half a cell outside it and read
       as a second border. The rack's spacing keeps toasts inside the log pane: clear of
       the sidebar (32 cols) and the pane's left border on the left, the pane border
       and scrollbar on the right, and the pane border plus footer below. Widths are
       then relative to the pane, so toasts shrink with small windows, and
       `DevpApp._trim_toasts` drops the oldest ones that don't fit vertically. */
    ToastRack {
        margin-bottom: 2;  /* raised while the search bar is open: _set_search_open */
        /* Horizontal offsets must be padding: the docked rack ignores side margins.
           Right: 1 col of padding + the rack's own 2-col (invisible) scrollbar gutter
           clears the log's scrollbar and the pane border. */
        padding: 0 1 0 33;
    }
    Toast {
        width: 44;
        max-width: 100%;
        margin-top: 0;
        padding: 0 1;
        background: transparent;
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
    /* Body text uses the terminal's default text color (see ansi_color in __init__),
       so it stays readable on any terminal background. Accents stay themed. */
    ListView > ListItem,
    FooterKey .footer-key--description,
    FooterLabel,
    Toast.-information .toast--title,
    Toast.-warning .toast--title,
    Toast.-error .toast--title {
        color: ansi_default;
    }
    #search-input {
        height: 3;
        border: round $accent;
        background: transparent;
    }
    """

    ENABLE_COMMAND_PALETTE = False

    # Only the everyday keys are shown in the footer (so it fits narrow windows);
    # `?` opens a help screen listing everything.
    BINDINGS = [
        ("s", "start_selected", "Run"),
        ("x", "stop_selected", "Stop"),
        ("r", "restart_selected", "Restart"),
        ("slash", "search", "Search"),
        ("question_mark", "help", "Help"),
        ("q", "quit", "Quit"),
        Binding("S", "start_all", "Run all", show=False),
        Binding("X", "stop_all", "Stop all", show=False),
        Binding("c", "clear_log", "Clear log", show=False),
        Binding("G", "follow_log", "Follow", show=False),
        Binding("n", "next_match", "Next match", show=False),
        Binding("N", "prev_match", "Prev match", show=False),
        Binding("escape", "close_search", "Close search", show=False),
        # Priority, so it wins over the screen's own silent copy binding.
        Binding("ctrl+c", "copy_or_quit", "Copy / Quit", show=False, priority=True),
    ]

    def __init__(self, manager: ProcessManager) -> None:
        # ansi_color: draw the base background and text in the terminal's *default*
        # colors instead of painting the theme's. Terminals pad the character grid with
        # a margin no app can draw in, filled with that default background, so painting
        # our own shows as a frame in every terminal; this matches it everywhere. The
        # theme still colors everything else (borders, glyphs, highlights, toasts), and
        # `_use_theme_as_terminal_colors` makes the defaults themselves Rosé Pine where
        # the terminal allows it.
        super().__init__(ansi_color=True)
        self.theme = "rose-pine"
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
        self._autostart: Worker[None] | None = None
        self._glyph_frame = 0
        self._flush_timer: Timer | None = None
        self._log_view = LogView(max_lines=MAX_BUFFER_LINES, id="log")
        self._log_pane = Vertical(id="log-pane")

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
            with self._log_pane:
                yield self._log_view
                yield Input(placeholder="› search logs", id="search-input")
        yield Footer()

    def _on_notify(self, event: Notify) -> None:
        super()._on_notify(event)
        self._trim_toasts()

    def on_resize(self, event: Resize) -> None:
        self._trim_toasts()

    def _trim_toasts(self) -> None:
        """Drop the oldest toasts that won't fit in the log pane at the current height.

        Textual shows every live notification, so a burst (e.g. autostart) in a short
        window would stack toasts off the top of the screen. Textual has no public API
        for dismissing a single notification, hence `_notifications` / `_unnotify`.
        """
        fits = max(1, (self.size.height - _TOAST_RESERVED_ROWS) // _TOAST_ROWS)
        notifications = list(self._notifications)
        if len(notifications) <= fits:
            return
        for notification in notifications[:-fits]:
            self._unnotify(notification, refresh=False)
        self._refresh_notifications()

    async def on_mount(self) -> None:
        """Focus the sidebar, hide the search bar, and autostart configured processes."""
        self._use_theme_as_terminal_colors()
        self.query_one("#search-input", Input).display = False
        sidebar = self.query_one("#sidebar", ListView)
        sidebar.border_title = "Processes"
        sidebar.focus()
        self._refresh_log_pane()
        self.set_interval(_GLYPH_FRAME_INTERVAL, self._tick_sidebar)
        # In the background: waiting on dependencies' ready checks mustn't block the UI.
        self._autostart = self.run_worker(self.manager.autostart(), name="autostart")

    def _use_theme_as_terminal_colors(self) -> None:
        """Set the terminal's default background and text colors to the theme's until exit.

        With these (OSC 11 / OSC 10) the base colors, and the terminal's padding, are
        Rosé Pine. Terminals that don't support them ignore them, and devp simply sits
        on the terminal's own colors with no mismatched frame and readable text.
        """
        if self._driver is not None:
            background = _osc_color(self.theme_variables["background"])
            foreground = _osc_color(self.theme_variables["foreground"])
            self._driver.write(f"\x1b]11;{background}\x07\x1b]10;{foreground}\x07")

    def on_unmount(self) -> None:
        # Runs on every exit path, while the terminal is still ours: restore the
        # terminal's own default colors (OSC 111 / OSC 110).
        if self._driver is not None:
            self._driver.write("\x1b]111\x07\x1b]110\x07")

    def _update_log_title(self) -> None:
        if not self._log_pane.is_attached:  # the sidebar timer can fire during shutdown
            return
        title = _log_title(self.selected_name, self._selected_process())
        if self._log_pane.border_title != title:
            self._log_pane.border_title = title
        below = self._log_view.lines_below
        subtitle = (
            f"[$accent]▼ {below} more line{'s' if below != 1 else ''} · G to follow[/]"
            if below
            else ""
        )
        if self._log_pane.border_subtitle != subtitle:
            self._log_pane.border_subtitle = subtitle

    def _tick_sidebar(self) -> None:
        """Advance animated status glyphs and keep cron 'next run' countdowns live.

        Only rows whose glyph animates, or that show a countdown, are recomputed, and
        `set_status` skips the redraw when the label text hasn't changed.
        """
        self._glyph_frame += 1
        for name, item in self._list_items.items():
            runnable = self.manager.processes[name]
            state = runnable.state
            if is_animated(state) or isinstance(runnable, CronJob):
                item.set_status(state, _status_detail(runnable), self._glyph_frame)
        self._update_log_title()  # uptime ticks; pid/exit code change with the state

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
            item.set_status(message.state, detail, self._glyph_frame)

        name = escape(message.process_name)
        if message.state == ProcessState.RUNNING:
            runnable = self.manager.processes.get(message.process_name)
            has_check = runnable is not None and getattr(runnable.config, "has_ready_check", False)
            verb = "is ready" if has_check else "started"
            self.notify(f"'{name}' {verb}", severity="information", timeout=3)
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
        self._update_log_title()
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
        self._set_search_open(True)
        search_input.focus()

    def _set_search_open(self, is_open: bool) -> None:
        """Lift the toast stack above the search bar while it's open.

        Set inline on the rack rather than via a class on the app, which would make
        Textual restyle every widget and noticeably slow opening/closing search.
        """
        for rack in self.screen.query("ToastRack"):
            rack.styles.margin = (0, 0, 5 if is_open else 2, 0)

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
        self._set_search_open(False)
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

    def action_start_all(self) -> None:
        """Start every process and cron schedule, in dependency order (bound to 'S')."""
        self.notify("Starting everything", severity="information", timeout=2)
        self.run_worker(self.manager.start_all(), group="bulk", exclusive=True)

    def action_stop_all(self) -> None:
        """Stop every process and cron schedule, dependents first (bound to 'X')."""
        if self._autostart is not None:
            self._autostart.cancel()
        self.notify("Stopping everything", severity="information", timeout=2)
        # Same exclusive group as start_all: stopping cancels a start-all in progress.
        self.run_worker(self.manager.stop_all(), group="bulk", exclusive=True)

    def action_help(self) -> None:
        """Show every key and mouse action (bound to '?')."""
        self.push_screen(HelpScreen())

    def action_follow_log(self) -> None:
        """Jump to the newest output and keep following it (bound to 'G')."""
        self._log_view.scroll_end(animate=False)

    def action_clear_log(self) -> None:
        """Clear the selected process's log (bound to 'c')."""
        process = self._selected_process()
        if process is not None:
            process.output.clear()
            self._refresh_log_pane()

    def _selected_process(self):
        """Return the `ManagedProcess` for the sidebar selection, if any."""
        if self.selected_name is None:
            return None
        return self.manager.processes.get(self.selected_name)

    async def on_process_list_item_double_clicked(
        self, message: ProcessListItem.DoubleClicked
    ) -> None:
        """Start (or, for a cron job, run now) the double-clicked process."""
        process = self.manager.processes.get(message.process_name)
        if process is not None:
            await process.start()

    async def action_copy_or_quit(self) -> None:
        """Copy the selected log text (bound to Ctrl+C); with nothing selected, quit."""
        text = self.screen.get_selected_text()
        if not text:
            await self.action_quit()
            return
        self.copy_to_clipboard(text)  # OSC 52, for terminals that support it
        self.run_worker(partial(copy_native, text), thread=True, exit_on_error=False)
        self.screen.clear_selection()
        lines = text.count("\n") + 1
        summary = f"{lines} lines" if lines > 1 else f"{len(text)} characters"
        self.notify(f"Copied {summary}", severity="information", timeout=2)

    async def action_quit(self) -> None:
        """Stop every managed process before exiting, so none are left orphaned."""
        if self._autostart is not None:
            self._autostart.cancel()  # don't let a pending start spawn after shutdown
        await self.manager.shutdown_all()
        self.exit()
