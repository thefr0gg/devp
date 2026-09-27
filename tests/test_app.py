import asyncio
import sys

import pytest
from textual.widgets import Input, ListView

from devp.app import DevpApp
from devp.config import Config, ProcessConfig
from devp.manager import ProcessManager
from devp.process import ProcessState

pytestmark = pytest.mark.asyncio


def python_command(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


def make_app(**process_kwargs) -> DevpApp:
    config = Config(
        processes=[
            ProcessConfig(
                name="sleeper",
                command=python_command("import time; time.sleep(30)"),
                **process_kwargs,
            )
        ],
        crons=[],
    )
    return DevpApp(ProcessManager(config))


async def test_escape_closes_search_restores_focus_and_clears_text():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar", ListView)
        search_input = app.query_one("#search-input", Input)

        await pilot.press("slash")
        await pilot.pause()
        assert search_input.has_focus

        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("escape")
        await pilot.pause()

        assert search_input.display is False
        assert search_input.value == ""
        assert sidebar.has_focus is True
        assert search_input.has_focus is False

        await app.manager.shutdown_all()


async def test_keybindings_work_again_after_closing_search():
    """Regression test: a stale-focused hidden search input must not swallow keystrokes."""
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]

        await pilot.press("slash")
        await pilot.press("x")  # typed into the search box, must NOT stop the process
        await pilot.press("escape")
        await pilot.pause()
        assert sleeper.state == ProcessState.RUNNING

        await pilot.press("x")  # now that search is closed, this should reach the binding
        await asyncio.sleep(0.3)
        assert sleeper.state == ProcessState.STOPPED


async def test_submitting_search_clears_value_and_restores_focus():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one("#log")
        search_input = app.query_one("#search-input", Input)

        await pilot.press("slash")
        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()

        assert search_input.display is False
        assert search_input.value == ""
        assert log.has_focus is True

        await app.manager.shutdown_all()


async def test_escape_is_a_noop_when_search_is_not_open():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one("#log")
        log.focus()
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert log.has_focus is True  # escape must not steal focus when search is closed

        await app.manager.shutdown_all()


async def test_focused_pane_border_is_highlighted():
    app = make_app()
    async with app.run_test() as pilot:
        sidebar = app.query_one("#sidebar")
        log_pane = app.query_one("#log-pane")
        await pilot.pause()
        assert sidebar.styles.border_top[1] != log_pane.styles.border_top[1]
        sidebar_focused_color = sidebar.styles.border_top[1]

        app.query_one("#log").focus()
        await pilot.pause()
        assert log_pane.styles.border_top[1] == sidebar_focused_color
        assert sidebar.styles.border_top[1] != sidebar_focused_color
        assert log_pane.border_title == "Logs · sleeper"


async def test_search_highlights_and_navigates_matches():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]
        for i in range(30):
            sleeper.log(f"{'needle' if i % 10 == 0 else 'hay'} {i}")
        await asyncio.sleep(0.1)
        log = app.query_one("#log")
        assert log.line_count == 30

        await pilot.press("slash")
        for ch in "needle":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        matches = log.find("needle")
        assert len(matches) == 3
        assert app._search_cursor == matches[0]

        await pilot.press("n")
        assert app._search_cursor == matches[1]
        await pilot.press("N", "N")
        assert app._search_cursor == matches[2]  # wrapped around

        await app.manager.shutdown_all()


async def test_output_of_unselected_processes_is_not_written_to_the_log():
    config = Config(
        processes=[
            ProcessConfig(name="a", command=python_command("import time; time.sleep(30)")),
            ProcessConfig(name="b", command=python_command("import time; time.sleep(30)")),
        ],
        crons=[],
    )
    app = DevpApp(ProcessManager(config))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.manager.processes["b"].log("from b")
        app.manager.processes["a"].log("from a")
        await asyncio.sleep(0.1)
        log = app.query_one("#log")
        assert log.find("from") == [log.end_index - 1]

        await pilot.press("down")  # select 'b': its buffered output is replayed
        await pilot.pause()
        assert log.line_count == 1 and log.find("from b")

        await app.manager.shutdown_all()


async def test_running_process_glyph_animates():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        item = app._list_items["sleeper"]
        await asyncio.sleep(0.15)
        first = item.label_text
        await asyncio.sleep(0.25)
        assert item.label_text != first

        await app.manager.shutdown_all()
