import sys

import pytest

from devp.app import DevpApp
from devp.config import Config, ProcessConfig
from devp.manager import ProcessManager
from devp.mascot import MASCOT_HEIGHT, MASCOT_WIDTH, render_mascot


def test_the_mascot_is_30_columns_by_15_lines():
    lines = render_mascot().split("\n")
    assert (MASCOT_WIDTH, MASCOT_HEIGHT) == (30, 15)
    assert len(lines) == MASCOT_HEIGHT
    assert all(line.cell_len <= MASCOT_WIDTH for line in lines)
    assert set(render_mascot().plain) <= {"▀", "▄", " ", "\n"}


def app_with(count: int) -> DevpApp:
    processes = [
        ProcessConfig(name=f"p{i}", command=[sys.executable, "-c", "pass"], autostart=False)
        for i in range(count)
    ]
    return DevpApp(ProcessManager(Config(processes=processes, crons=[])))


async def mascot_shown(app: DevpApp, size: tuple[int, int]) -> bool:
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        app._update_mascot()
        await pilot.pause()
        shown = app.query_one("#mascot").display
        await app.manager.shutdown_all()
        return shown


@pytest.mark.asyncio
async def test_the_mascot_shows_when_there_is_room_below_the_processes():
    assert await mascot_shown(app_with(3), (100, 30))


@pytest.mark.asyncio
async def test_the_mascot_hides_in_a_short_window():
    assert not await mascot_shown(app_with(3), (100, 16))


@pytest.mark.asyncio
async def test_the_mascot_hides_when_the_process_list_needs_the_space():
    assert not await mascot_shown(app_with(14), (100, 30))
