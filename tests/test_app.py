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
