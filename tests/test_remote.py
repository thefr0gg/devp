import asyncio
import sys

import pytest
import pytest_asyncio

from devp.api import API_FILE_NAME, ControlServer
from devp.app import DevpApp
from devp.config import Config, CronConfig, ProcessConfig
from devp.manager import ProcessManager
from devp.process import ProcessState
from devp.remote import RemoteManager

pytestmark = pytest.mark.asyncio


async def _wait_for(predicate, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def _noop() -> None:
    pass


@pytest_asyncio.fixture
async def host(tmp_path):
    config = Config(
        processes=[
            ProcessConfig(
                name="ticker",
                command=[
                    sys.executable,
                    "-u",
                    "-c",
                    "print('hi', flush=True); import time; time.sleep(30)",
                ],
                autostart=False,
            )
        ],
        crons=[
            CronConfig(
                name="job",
                command=[sys.executable, "-c", "print('ran')"],
                schedule="0 0 1 1 *",
            )
        ],
    )
    manager = ProcessManager(config)
    manager.build()
    server = ControlServer(lambda: manager, _noop)
    file = tmp_path / API_FILE_NAME
    await server.start(file)
    yield manager, file
    await manager.shutdown_all()
    await server.stop()


async def test_remote_manager_mirrors_entries_and_existing_output(host):
    manager, file = host
    await manager.processes["ticker"].start()
    await _wait_for(lambda: "hi" in manager.processes["ticker"].output)
    await manager.processes["job"].start_schedule()
    await _wait_for(lambda: manager.processes["job"].next_run_at is not None)

    remote = await asyncio.to_thread(RemoteManager, file)

    assert list(remote.processes) == ["ticker", "job"]
    ticker, job = remote.processes["ticker"], remote.processes["job"]
    assert ticker.state == ProcessState.RUNNING and ticker.pid
    assert list(ticker.output) == ["hi"]
    assert job.is_cron and job.state == ProcessState.SCHEDULED and job.next_run_at is not None
    assert not ticker.is_cron


async def test_polling_delivers_new_lines_once_and_state_changes(host):
    manager, file = host
    remote = await asyncio.to_thread(RemoteManager, file)
    lines, states = [], []
    remote.build(
        on_output=lambda name, line: lines.append((name, line)),
        on_state_change=lambda name, state: states.append((name, state)),
    )
    poller = asyncio.create_task(remote.autostart())
    try:
        await manager.processes["ticker"].start()
        await _wait_for(lambda: ("ticker", "hi") in lines)
        manager.processes["ticker"].log("again")
        await _wait_for(lambda: ("ticker", "again") in lines)
        await asyncio.sleep(0.8)  # more polls: nothing may repeat
        assert lines.count(("ticker", "hi")) == 1
        assert lines.count(("ticker", "again")) == 1
        assert ("ticker", ProcessState.RUNNING) in states
        assert list(remote.processes["ticker"].output) == ["hi", "again"]
    finally:
        poller.cancel()


async def test_start_stop_restart_go_to_the_host(host):
    manager, file = host
    remote = await asyncio.to_thread(RemoteManager, file)
    ticker = remote.processes["ticker"]

    await ticker.start()
    assert manager.processes["ticker"].state == ProcessState.RUNNING
    await ticker.restart()
    assert manager.processes["ticker"].start_count == 2
    await ticker.stop()
    assert manager.processes["ticker"].state == ProcessState.STOPPED

    await remote.start_all()
    assert manager.processes["ticker"].state == ProcessState.RUNNING
    await remote.stop_all()
    assert manager.processes["ticker"].state == ProcessState.STOPPED


async def test_shutdown_all_only_detaches_and_leaves_the_host_running(host):
    manager, file = host
    await manager.processes["ticker"].start()
    remote = await asyncio.to_thread(RemoteManager, file)
    await remote.shutdown_all()
    assert manager.processes["ticker"].state == ProcessState.RUNNING


async def test_a_failed_request_is_reported_not_raised(tmp_path, host):
    manager, file = host
    remote = await asyncio.to_thread(RemoteManager, file)
    errors = []
    remote.build(on_error=lambda name, text: errors.append((name, text)))
    remote._api_file = tmp_path / "gone.json"
    await remote.processes["ticker"].start()
    assert errors and errors[0][0] == "devp"


async def test_losing_the_host_calls_on_disconnect(host, monkeypatch):
    manager, file = host
    monkeypatch.setattr("devp.remote._POLL_INTERVAL", 0.01)
    remote = await asyncio.to_thread(RemoteManager, file)
    disconnected = asyncio.Event()
    remote.on_disconnect = disconnected.set
    remote._api_file = file.parent / "gone.json"
    await asyncio.wait_for(remote.autostart(), 5)
    assert disconnected.is_set()


async def test_the_tui_attached_to_a_host_shows_and_controls_it(host):
    manager, file = host
    await manager.processes["ticker"].start()
    remote = await asyncio.to_thread(RemoteManager, file)
    app = DevpApp(remote, enable_api=False)
    async with app.run_test() as pilot:
        await _wait_for(lambda: len(remote.processes["ticker"].output) > 0)
        await pilot.press("x")
        await _wait_for(lambda: manager.processes["ticker"].state == ProcessState.STOPPED)
        await pilot.press("s")
        await _wait_for(lambda: manager.processes["ticker"].state == ProcessState.RUNNING)
        await pilot.press("q")
        await asyncio.sleep(0.3)
    assert not app.is_running
    assert manager.processes["ticker"].state == ProcessState.RUNNING  # quitting the view only


async def test_a_swapped_host_manager_is_reported_to_the_remote(host, monkeypatch):
    manager, file = host
    monkeypatch.setattr("devp.remote._POLL_INTERVAL", 0.01)
    remote = await asyncio.to_thread(RemoteManager, file)
    changed = asyncio.Event()
    remote.on_reconfigured = changed.set

    poller = asyncio.create_task(remote.autostart())
    await asyncio.sleep(0.1)
    assert not changed.is_set()  # same manager: nothing to report

    # What a config reload does: the server is now looking at a different manager.
    replacement = ProcessManager(Config(processes=[], crons=[]))
    replacement.build()
    server_lookup = remote._api_file  # the same API file, served by the fixture's server
    assert server_lookup == file
    from devp import api

    original = api.ControlServer._dispatch
    state = {"manager": replacement}

    async def dispatch(self, method, params):
        self._get_manager = lambda: state["manager"]
        return await original(self, method, params)

    monkeypatch.setattr(api.ControlServer, "_dispatch", dispatch)
    await asyncio.wait_for(changed.wait(), 5)
    await asyncio.wait_for(poller, 5)  # the stale poll loop ends itself


async def test_the_attached_tui_rebuilds_when_the_host_reloads(tmp_path):
    from devp.__main__ import _attach  # noqa: F401  (imported to make sure it still exists)

    manager, file = None, None
    config = Config(
        processes=[
            ProcessConfig(name="old", command=[sys.executable, "-c", "pass"], autostart=False)
        ],
        crons=[],
    )
    holder = {"manager": ProcessManager(config)}
    holder["manager"].build()
    server = ControlServer(lambda: holder["manager"], _noop)
    file = tmp_path / API_FILE_NAME
    await server.start(file)
    try:
        remote = await asyncio.to_thread(RemoteManager, file)
        app = DevpApp(remote, enable_api=False)

        def follow(current):
            async def swap():
                fresh = await asyncio.to_thread(RemoteManager, file)
                follow(fresh)
                await app.adopt_manager(fresh)

            current.on_reconfigured = lambda: app.run_worker(swap(), group="reattach", exclusive=True)

        follow(remote)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._process_names == ["old"]

            new_config = Config(
                processes=[
                    ProcessConfig(name="new", command=[sys.executable, "-c", "pass"], autostart=False)
                ],
                crons=[],
            )
            holder["manager"] = ProcessManager(new_config)
            holder["manager"].build()
            await _wait_for(lambda: app._process_names == ["new"], timeout=10)
            assert app.selected_name == "new"
            await pilot.press("q")
            await asyncio.sleep(0.2)
    finally:
        await server.stop()
