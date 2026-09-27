import pytest
from textual.app import App, ComposeResult

from devp.log_view import LogView, _clean

pytestmark = pytest.mark.asyncio


class LogApp(App[None]):
    def __init__(self, max_lines: int = 100) -> None:
        super().__init__()
        self.max_lines = max_lines

    def compose(self) -> ComposeResult:
        yield LogView(max_lines=self.max_lines, id="log")


@pytest.mark.filterwarnings("ignore")
def test_clean_strips_ansi_and_control_characters():
    assert _clean("\x1b[32mgreen\x1b[0m\tok\x1b]0;title\x07") == "green   ok"
    assert _clean("a\x00b") == "a�b"


async def test_long_lines_wrap_to_extra_rows():
    app = LogApp()
    async with app.run_test(size=(22, 10)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        width = log.scrollable_content_region.width
        log.write_lines(["short", "x" * (width * 2 + 1)])
        await pilot.pause()
        assert log.virtual_size.height == 1 + 3


async def test_prunes_to_max_lines_keeping_absolute_indices():
    app = LogApp(max_lines=5)
    async with app.run_test(size=(40, 10)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        log.write_lines(f"line {i}" for i in range(12))
        assert log.line_count == 5
        assert log.virtual_size.height == 5
        assert log.find("line 1") == [10, 11]
        assert log.end_index == 12


async def test_follows_output_only_when_scrolled_to_the_end():
    app = LogApp(max_lines=1000)
    async with app.run_test(size=(40, 10)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        log.write_lines(f"line {i}" for i in range(100))
        await pilot.pause()
        assert log.is_vertical_scroll_end

        log.scroll_to(y=10, animate=False, immediate=True)
        log.write_lines(["more"])
        await pilot.pause()
        assert log.scroll_y == 10


async def test_scroll_to_line_accounts_for_wrapped_rows():
    app = LogApp()
    async with app.run_test(size=(22, 6)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        width = log.scrollable_content_region.width
        log.write_lines(["x" * (width * 3)] + [f"line {i}" for i in range(20)])
        log.scroll_to_line(log.find("line 5")[0])
        await pilot.pause()
        assert log.scroll_y == 3 + 5


async def test_screen_offsets_map_wrapped_rows_back_to_the_original_line():
    app = LogApp()
    async with app.run_test(size=(22, 10)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        width = log.scrollable_content_region.width
        long_line = "".join(chr(ord("a") + i % 26) for i in range(width * 2))
        log.write_lines(["first", long_line])
        await pilot.pause()
        origin = log.content_region.offset

        # Row 0 is "first" (line 0); rows 1-2 are the two halves of line 1.
        widget, offset = app.screen.get_widget_and_offset_at(origin.x + 2, origin.y)
        assert widget is log and tuple(offset) == (2, 0)
        widget, offset = app.screen.get_widget_and_offset_at(origin.x + 3, origin.y + 2)
        assert tuple(offset) == (width + 3, 1)


async def test_selection_extracts_text_across_lines_and_survives_pruning():
    from textual.geometry import Offset
    from textual.selection import Selection

    app = LogApp(max_lines=4)
    async with app.run_test(size=(40, 10)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        log.write_lines(["alpha", "bravo", "charlie"])
        selection = Selection(Offset(2, 0), Offset(3, 2))
        assert log.get_selection(selection) == ("pha\nbravo\ncha", "\n")

        # Offsets are absolute line indices: pruning 'alpha' keeps the rest selected.
        log.write_lines(["delta", "echo"])
        assert log.get_selection(selection) == ("bravo\ncha", "\n")


async def test_selected_text_keeps_its_own_color():
    from textual.geometry import Offset
    from textual.selection import Selection

    app = LogApp()
    async with app.run_test(size=(40, 10)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        log.write_lines(["hello world"])
        app.screen.selections = {log: Selection(Offset(0, 0), Offset(5, 0))}
        await pilot.pause()
        selected = next(seg for seg in log.render_line(0) if seg.text.startswith("hello"))
        assert selected.style.bgcolor is not None
        assert selected.style.color != selected.style.bgcolor  # text stays readable


async def test_ansi_colors_are_rendered_while_text_stays_plain():
    app = LogApp()
    async with app.run_test(size=(40, 10)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        log.write_lines(["\x1b]0;title\x07\x1b[31mERROR\x1b[0m\tdisk full", "plain line"])
        await pilot.pause()

        assert log.find("error\tdisk") == []  # tabs are expanded in the plain text
        assert log.find("ERROR   disk full") == [0]
        segments = {seg.text: seg.style for seg in log.render_line(0) if seg.text.strip()}
        assert segments["ERROR"].color.number == 1  # ANSI red, from the terminal palette
        assert segments["   disk full"].color != segments["ERROR"].color  # reset after ERROR

        from textual.geometry import Offset
        from textual.selection import Selection

        # Offsets are on the plain text: selecting "disk" copies "disk".
        assert log.get_selection(Selection(Offset(8, 0), Offset(12, 0)))[0] == "disk"


async def test_raw_colored_lines_are_pruned_with_the_buffer():
    app = LogApp(max_lines=3)
    async with app.run_test(size=(40, 10)) as pilot:
        log = app.query_one(LogView)
        await pilot.pause()
        log.write_lines(f"\x1b[32mline {i}\x1b[0m" for i in range(10))
        assert sorted(log._ansi) == [7, 8, 9]
        log.clear()
        assert log._ansi == {}
