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
