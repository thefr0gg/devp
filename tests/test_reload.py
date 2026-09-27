import asyncio
import sys

import pytest

from devp.app import DevpApp
from devp.config import load_config
from devp.manager import ProcessManager
from devp.messages import ProcessStateChanged
from devp.process import ProcessState
from devp.screens import ReloadConfigScreen

pytestmark = pytest.mark.asyncio

SLEEP = f'["{sys.executable}", "-c", "import time; time.sleep(30)"]'.replace("\\", "\\\\")


def entry(name: str, extra: str = "") -> str:
    return f'[[process]]\nname = "{name}"\ncommand = {SLEEP}\n{extra}\n'


async def wait_for(predicate, timeout=8.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setattr("devp.app._CONFIG_POLL_INTERVAL", 0.2)
    path = tmp_path / "devp.toml"
    path.write_text(entry("api"))
    return path


def make_app(path):
    return DevpApp(ProcessManager(load_config(path)), config_path=path)


async def test_confirming_a_reload_rebuilds_from_the_new_config(config_file):
    app = make_app(config_file)
    async with app.run_test() as pilot:
        old_api = app.manager.processes["api"]
        await wait_for(lambda: old_api.state == ProcessState.RUNNING)

        config_file.write_text(entry("api") + entry("worker"))
        await wait_for(lambda: isinstance(app.screen, ReloadConfigScreen))
        await pilot.press("y")

        await wait_for(lambda: list(app.manager.processes) == ["api", "worker"])
        await wait_for(
            lambda: all(p.state == ProcessState.RUNNING for p in app.manager.processes.values())
        )
        assert old_api.state == ProcessState.STOPPED  # the old run was shut down
        assert app.manager.processes["api"] is not old_api
        assert list(app._list_items) == ["api", "worker"]
        assert len(app.query_one("#sidebar").children) == 2
        await app.manager.shutdown_all()


async def test_declining_keeps_running_and_does_not_ask_again(config_file):
    app = make_app(config_file)
    async with app.run_test() as pilot:
        api = app.manager.processes["api"]
        await wait_for(lambda: api.state == ProcessState.RUNNING)

        config_file.write_text(entry("api") + entry("worker"))
        await wait_for(lambda: isinstance(app.screen, ReloadConfigScreen))
        await pilot.press("n")
        await asyncio.sleep(0.3)
        assert not isinstance(app.screen, ReloadConfigScreen)
        assert list(app.manager.processes) == ["api"] and api.state == ProcessState.RUNNING
        await app.manager.shutdown_all()


async def test_a_broken_config_is_reported_and_the_old_one_keeps_running(config_file):
    app = make_app(config_file)
    async with app.run_test(notifications=True) as pilot:
        api = app.manager.processes["api"]
        await wait_for(lambda: api.state == ProcessState.RUNNING)

        config_file.write_text(entry("api") + '[[process]]\nname = "broken"\n')
        await wait_for(lambda: any("has an error" in n.title for n in app._notifications))
        await pilot.pause()
        assert not isinstance(app.screen, ReloadConfigScreen)
        assert api.state == ProcessState.RUNNING
        await app.manager.shutdown_all()


async def test_a_save_without_effective_changes_does_not_prompt(config_file):
    app = make_app(config_file)
    async with app.run_test():
        config_file.write_text("# just a comment\n" + entry("api"))
        await asyncio.sleep(0.4)
        assert not isinstance(app.screen, ReloadConfigScreen)
        await app.manager.shutdown_all()


async def test_events_from_a_previous_generation_are_ignored(config_file):
    app = make_app(config_file)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._generation = 1  # as if a reload happened
        item = app._list_items["api"]
        app.post_message(ProcessStateChanged("api", ProcessState.CRASHED, generation=0))
        await pilot.pause()
        assert "✗" not in item.label_text  # the stale "crashed" event was dropped
        await app.manager.shutdown_all()
