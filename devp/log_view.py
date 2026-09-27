"""A scrollback widget that wraps long lines but only renders the rows actually on screen."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable

from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual.cache import LRUCache
from textual.events import Resize
from textual.geometry import Offset, Size
from textual.scroll_view import ScrollView
from textual.selection import Selection
from textual.strip import Strip

# ANSI escape sequences (colors, cursor movement, window titles) that processes print.
_sub_ansi = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])").sub
_sub_control = re.compile("[\u0000-\u001f\u007f]").sub


def _clean(line: str) -> str:
    """Strip ANSI escapes, expand tabs, and replace other control characters.

    Every remaining character then has a predictable cell width, which the row
    counting in `LogView` relies on.
    """
    if "\x1b" in line:
        line = _sub_ansi("", line)
    return _sub_control("�", line.expandtabs())


class LogView(ScrollView, can_focus=True):
    """Holds up to `max_lines` lines of output, soft-wrapped to the widget's width.

    Unlike `RichLog`, which renders (and wraps) every line as it is written, this keeps
    the raw strings and renders only the rows scrolled into view, so the cost of a
    write doesn't depend on how fast a process is producing output. Each line's wrapped
    height is cheap to compute (lines that fit need no wrapping at all), which keeps
    the scrollbar exact without rendering anything off screen.

    Lines are addressed by an absolute index that stays stable as old lines are pruned
    from the front, so callers can hold on to it (e.g. for search navigation).

    Supports mouse text selection: each rendered row is tagged with its line's absolute
    index and the character offset it starts at, so a selection maps back to the
    original (unwrapped) text and stays on the same content as the log scrolls.
    """

    ALLOW_SELECT = True

    DEFAULT_CSS = """
    LogView {
        overflow-x: hidden;
        overflow-y: scroll;
    }
    """

    def __init__(self, max_lines: int, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.max_lines = max_lines
        self._lines: list[str] = []
        self._row_starts: list[int] = []  # first wrapped row of each line
        self._total_rows = 0
        self._first_index = 0  # absolute index of self._lines[0]
        self._wrap_width = 0
        self._highlight = ""
        # Per line: its wrapped rows, each with the character offset it starts at.
        self._row_cache: LRUCache[int, list[tuple[Strip, int]]] = LRUCache(1024)
        # Finished screen rows (cropped, tagged with selection offsets, styled), keyed by
        # (line index, wrapped row, width); tagging is costly enough to be worth caching.
        self._line_cache: LRUCache[tuple[int, int, int], Strip] = LRUCache(1024)

    def _clear_caches(self) -> None:
        self._row_cache.clear()
        self._line_cache.clear()

    @property
    def line_count(self) -> int:
        return len(self._lines)

    @property
    def end_index(self) -> int:
        """Absolute index one past the last line."""
        return self._first_index + len(self._lines)

    def clear(self) -> None:
        """Remove every line."""
        self._first_index += len(self._lines)
        self._lines.clear()
        self._row_starts.clear()
        self._total_rows = 0
        self._clear_caches()
        self._update_virtual_size()
        self.scroll_home(animate=False, immediate=True)

    def write_lines(self, lines: Iterable[str]) -> None:
        """Append lines, following the output if the view was already at the bottom."""
        was_at_end = self.is_vertical_scroll_end
        width = self._wrap_width
        row_starts = self._row_starts
        total = self._total_rows
        for line in lines:
            line = _clean(line)
            self._lines.append(line)
            row_starts.append(total)
            total += self._row_count(line, width)
        self._total_rows = total
        self._prune()
        self._update_virtual_size()
        if was_at_end and not self.is_vertical_scrollbar_grabbed:
            self.scroll_end(animate=False, immediate=True, x_axis=False)
        self.refresh()

    def set_highlight(self, query: str) -> None:
        """Highlight lines containing `query` (case-insensitive); an empty query clears it."""
        query = query.lower()
        if query != self._highlight:
            self._highlight = query
            self._clear_caches()
            self.refresh()

    def find(self, query: str) -> list[int]:
        """Absolute indices of the lines containing `query` (case-insensitive)."""
        query = query.lower()
        if not query:
            return []
        first = self._first_index
        return [first + i for i, line in enumerate(self._lines) if query in line.lower()]

    def scroll_to_line(self, index: int) -> None:
        """Scroll so the line with absolute `index` is at the top of the view."""
        i = index - self._first_index
        if 0 <= i < len(self._lines):
            self.scroll_to(y=self._row_starts[i], animate=False)

    def _prune(self) -> None:
        excess = len(self._lines) - self.max_lines
        if excess <= 0:
            return
        shift = self._row_starts[excess]
        del self._lines[:excess]
        self._row_starts = [start - shift for start in self._row_starts[excess:]]
        self._total_rows -= shift
        self._first_index += excess
        # Keep the view on the same content rather than letting it drift by `shift` rows.
        if not self.is_vertical_scroll_end:
            self.scroll_to(y=max(0, self.scroll_y - shift), animate=False, immediate=True)

    @staticmethod
    def _row_count(line: str, width: int) -> int:
        """How many rows `line` wraps to; must agree with `_wrap`."""
        if width <= 0:
            return 1
        if (len(line) if line.isascii() else cell_len(line)) <= width:
            return 1
        return len(_wrap(Text(line), width))

    def _update_virtual_size(self) -> None:
        self.virtual_size = Size(self._wrap_width, self._total_rows)

    def on_resize(self, event: Resize) -> None:
        width = self.scrollable_content_region.width
        if width == self._wrap_width:
            return
        was_at_end = self.is_vertical_scroll_end
        self._wrap_width = width
        self._clear_caches()
        total = 0
        row_starts = self._row_starts
        for i, line in enumerate(self._lines):
            row_starts[i] = total
            total += self._row_count(line, width)
        self._total_rows = total
        self._update_virtual_size()
        if was_at_end:
            self.scroll_end(animate=False, immediate=True, x_axis=False)

    def notify_style_update(self) -> None:
        super().notify_style_update()
        self._clear_caches()

    def _match_style(self) -> Style:
        """Search-match highlight in the current theme's colors (dark text on warning)."""
        variables = self.app.theme_variables
        return Style(color=variables["background"], bgcolor=variables["warning"])

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """The selected text; selection offsets use absolute line indices."""
        if not self._lines:
            return None
        first = self._first_index

        def to_local(offset: Offset | None) -> Offset | None:
            # Lines pruned since the selection began are gone; start from the oldest kept.
            if offset is None or offset.y >= first:
                return None if offset is None else Offset(offset.x, offset.y - first)
            return Offset(0, 0)

        local = Selection(to_local(selection.start), to_local(selection.end))
        return local.extract("\n".join(self._lines)), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        rich_style = self.rich_style
        row = self.scroll_offset.y + y
        if row >= self._total_rows or not self._lines:
            return Strip.blank(width, rich_style)
        i = bisect_right(self._row_starts, row) - 1
        index = self._first_index + i
        sub = row - self._row_starts[i]
        selection = self.text_selection
        selected = selection is not None and selection.get_span(index) is not None
        key = (index, sub, width)
        if not selected and (cached := self._line_cache.get(key)) is not None:
            return cached
        rows = self._render_rows(i)
        if sub >= len(rows):
            return Strip.blank(width, rich_style)
        strip, char_start = rows[sub]
        line = (
            strip.crop_extend(0, width, rich_style)
            .apply_offsets(char_start, index)
            .apply_style(rich_style)
        )
        if not selected:
            self._line_cache[key] = line
        return line

    def _render_rows(self, i: int) -> list[tuple[Strip, int]]:
        index = self._first_index + i
        selection = self.text_selection
        span = selection.get_span(index) if selection is not None else None
        if span is None:
            cached = self._row_cache.get(index)
            if cached is not None:
                return cached
        line = self._lines[i]
        text = Text(line, end="")
        if self._highlight and self._highlight in line.lower():
            text.stylize(self._match_style())
        if span is not None:
            start, end = span
            # Only take the selection background: the theme's selection foreground is
            # transparent, which a Rich style resolves to the background (hiding text).
            selection_style = self.screen.get_component_rich_style("screen--selection")
            text.stylize(
                Style(bgcolor=selection_style.bgcolor),
                start,
                len(line) if end == -1 else end,
            )
        console = self.app.console
        rows: list[tuple[Strip, int]] = []
        char_start = 0
        for row in _wrap(text, self._wrap_width):
            # Wrapping can drop the whitespace at a break, so locate each row in the line.
            found = line.find(row.plain, char_start) if row.plain else -1
            if found >= 0:
                char_start = found
            rows.append((Strip(row.render(console), row.cell_len), char_start))
            char_start += len(row.plain)
        if span is None:
            self._row_cache[index] = rows
        return rows


_wrap_console: Console | None = None


def _wrap(text: Text, width: int) -> list[Text]:
    """Word-wrap `text` to `width` cells (folding words longer than a row)."""
    if width <= 0 or text.cell_len <= width:
        return [text]
    global _wrap_console
    if _wrap_console is None:
        # Wrapping only needs a console for its settings; the width is passed explicitly.
        _wrap_console = Console(width=width, color_system=None, legacy_windows=False)
    return list(text.wrap(_wrap_console, width, overflow="fold"))
