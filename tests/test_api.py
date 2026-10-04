import asyncio
import json
import sys

import pytest
import pytest_asyncio

from devp.api import API_FILE_NAME, ApiError, ControlServer, call, find_api_file
from devp.app import DevpApp
from devp.config import Config, CronConfig, ProcessConfig
from devp.manager import ProcessManager
from devp.process import ProcessState

pytestmark = pytest.mark.asyncio


def python_command(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


def make_manager() -> ProcessManager:
    config = Config(
        processes=[
            ProcessConfig(
                name="sleeper",
                command=python_command("print('hello', flush=True); import time; time.sleep(30)"),
                autostart=False,
            )
        ],
        crons=[
            CronConfig(name="job", command=python_command("print('ran')"), schedule="0 0 1 1 *")
        ],
    )
    manager = ProcessManager(config)
    manager.build()
    return manager


async def noop_quit() -> None:
    pass


async def rpc(file, method, **params):
    """`call` is blocking, so keep it off the event loop the server runs on."""
    return await asyncio.to_thread(call, file, method, params)


@pytest_asyncio.fixture
async def server(tmp_path):
    manager = make_manager()
    quit_called = asyncio.Event()

    async def on_quit():
        quit_called.set()

    srv = ControlServer(lambda: manager, on_quit)
    file = tmp_path / API_FILE_NAME
    await srv.start(file)
    srv.manager, srv.file, srv.quit_called = manager, file, quit_called
    yield srv
    await manager.shutdown_all()
    await srv.stop()


async def test_discovery_file_has_port_token_and_is_removed_on_stop(tmp_path):
    srv = ControlServer(make_manager, noop_quit)
    file = tmp_path / API_FILE_NAME
    await srv.start(file)
    info = json.loads(file.read_text())
    assert info["port"] == srv.port and info["host"] == "127.0.0.1" and info["token"]
    assert find_api_file(tmp_path) == file
    await srv.stop()
    assert not file.exists()


async def test_stop_leaves_a_newer_servers_file_alone(tmp_path):
    first = ControlServer(make_manager, noop_quit)
    second = ControlServer(make_manager, noop_quit)
    file = tmp_path / API_FILE_NAME
    await first.start(file)
    await second.start(file)  # overwrites the file
    await first.stop()
    assert file.exists()
    await second.stop()
    assert not file.exists()


async def test_ping_and_list(server):
    assert (await rpc(server.file, "ping"))["pong"] is True
    items = await rpc(server.file, "list")
    by_name = {item["name"]: item for item in items}
    assert by_name["sleeper"]["kind"] == "process"
    assert by_name["sleeper"]["state"] == "stopped"
    assert by_name["job"]["kind"] == "cron"
    assert by_name["job"]["state"] == "scheduled"
    assert by_name["job"]["schedule"] == "0 0 1 1 *"


async def test_start_stop_and_restart(server):
    started = await rpc(server.file, "start", name="sleeper")
    assert started["state"] == "running" and started["pid"]
    assert server.manager.processes["sleeper"].state == ProcessState.RUNNING

    restarted = await rpc(server.file, "restart", name="sleeper")
    assert restarted["state"] == "running" and restarted["restarts"] == 1

    stopped = await rpc(server.file, "stop", name="sleeper")
    assert stopped["state"] == "stopped" and stopped["pid"] is None


async def test_start_runs_a_cron_job_now_and_logs_returns_its_output(server):
    await rpc(server.file, "start", name="job")
    await server.manager.processes["job"]._process.wait()
    logs = await rpc(server.file, "logs", name="job")
    assert "ran" in logs["lines"]
    assert (await rpc(server.file, "logs", name="job", lines=1))["lines"] == ["ran"]
    assert (await rpc(server.file, "logs", name="job", lines=0))["lines"] == []


async def test_logs_strip_ansi_unless_raw(server):
    server.manager.processes["sleeper"].log("\x1b[31mred\x1b[0m")
    assert (await rpc(server.file, "logs", name="sleeper"))["lines"] == ["red"]
    raw = await rpc(server.file, "logs", name="sleeper", raw=True)
    assert raw["lines"] == ["\x1b[31mred\x1b[0m"]


async def test_wait_ready_returns_once_the_process_is_ready(tmp_path):
    config = Config(
        processes=[
            ProcessConfig(
                name="web",
                command=python_command("import time; print('up', flush=True); time.sleep(30)"),
                ready_when="up",
                autostart=False,
            )
        ],
        crons=[],
    )
    manager = ProcessManager(config)
    manager.build()
    srv = ControlServer(lambda: manager, noop_quit)
    file = tmp_path / API_FILE_NAME
    await srv.start(file)
    try:
        result = await rpc(file, "start", name="web", wait_ready=True)
        assert result["state"] == "running"
    finally:
        await manager.shutdown_all()
        await srv.stop()


async def test_start_all_and_stop_all(server):
    stop_all_hook = []
    server._on_stop_all = lambda: stop_all_hook.append(True)
    items = await rpc(server.file, "start_all")
    assert {i["name"]: i["state"] for i in items}["sleeper"] == "running"
    items = await rpc(server.file, "stop_all")
    assert {i["name"]: i["state"] for i in items}["sleeper"] == "stopped"
    assert stop_all_hook == [True]


@pytest.mark.parametrize(
    "method, params, message",
    [
        ("status", {"name": "nope"}, "no process named 'nope'"),
        ("start", {}, "'name' is required"),
        ("logs", {"name": "sleeper", "lines": -1}, "'lines' must be a non-negative integer"),
        ("logs", {"name": "sleeper", "lines": True}, "'lines' must be a non-negative integer"),
        ("explode", {}, "unknown method 'explode'"),
    ],
)
async def test_bad_requests_return_errors(server, method, params, message):
    with pytest.raises(ApiError, match=message):
        await rpc(server.file, method, **params)


async def _raw_exchange(server, payload: bytes) -> dict:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(payload)
    await writer.drain()
    response = json.loads(await reader.readline())
    writer.close()
    return response


async def test_wrong_or_missing_token_is_rejected_and_does_nothing(server):
    for request in (
        {"id": 7, "token": "wrong", "method": "start", "params": {"name": "sleeper"}},
        {"id": 8, "method": "start", "params": {"name": "sleeper"}},
    ):
        response = await _raw_exchange(server, json.dumps(request).encode() + b"\n")
        assert response["ok"] is False and "token" in response["error"]
    assert server.manager.processes["sleeper"].state == ProcessState.STOPPED


async def test_malformed_requests_get_an_error_not_a_dropped_server(server):
    for payload in (b"not json\n", b"[1, 2]\n"):
        response = await _raw_exchange(server, payload)
        assert response["ok"] is False
    assert (await rpc(server.file, "ping"))["pong"] is True  # still serving


async def test_responses_echo_the_request_id(server):
    token = json.loads(server.file.read_text())["token"]
    request = {"id": "abc", "token": token, "method": "ping"}
    response = await _raw_exchange(server, json.dumps(request).encode() + b"\n")
    assert response["id"] == "abc" and response["ok"] is True


async def test_multiple_requests_on_one_connection(server):
    token = json.loads(server.file.read_text())["token"]
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    for i in range(3):
        writer.write(json.dumps({"id": i, "token": token, "method": "ping"}).encode() + b"\n")
        await writer.drain()
        assert json.loads(await reader.readline())["id"] == i
    writer.close()


async def test_quit_answers_then_triggers_the_quit_callback(server):
    assert await rpc(server.file, "quit") == {"quitting": True}
    await asyncio.wait_for(server.quit_called.wait(), 2)


async def test_call_without_a_server_raises_api_error(tmp_path):
    file = tmp_path / API_FILE_NAME
    with pytest.raises(ApiError, match="can't reach devp"):
        call(file, "ping")  # file missing
    file.write_text(json.dumps({"host": "127.0.0.1", "port": 1, "token": "t"}))
    with pytest.raises(ApiError, match="can't reach devp"):
        call(file, "ping", timeout=1)  # nothing listening


async def test_find_api_file_searches_parents(tmp_path):
    file = tmp_path / API_FILE_NAME
    file.write_text("{}")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_api_file(nested) == file


async def test_app_serves_the_api_while_running_and_cleans_up(tmp_path):
    config_path = tmp_path / "devp.toml"
    config_path.write_text("")
    app = DevpApp(make_manager(), config_path=config_path)
    file = tmp_path / API_FILE_NAME
    async with app.run_test() as pilot:
        await pilot.pause()
        assert file.exists()
        started = await rpc(file, "start", name="sleeper")
        assert started["state"] == "running"
        await rpc(file, "quit")
        for _ in range(100):
            if not app.is_running:
                break
            await asyncio.sleep(0.05)
    assert not app.is_running
    assert app.manager.processes["sleeper"].state == ProcessState.STOPPED
    assert not file.exists()


async def test_app_without_the_api_writes_no_file(tmp_path):
    config_path = tmp_path / "devp.toml"
    config_path.write_text("")
    app = DevpApp(make_manager(), config_path=config_path, enable_api=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not (tmp_path / API_FILE_NAME).exists()
        await app.action_quit()


async def test_logs_return_a_cursor_and_since_returns_only_newer_lines(server):
    proc = server.manager.processes["sleeper"]
    for i in range(3):
        proc.log(f"line {i}")
    first = await rpc(server.file, "logs", name="sleeper")
    assert first["lines"] == ["line 0", "line 1", "line 2"] and first["next"] == 3

    proc.log("line 3")
    proc.log("line 4")
    later = await rpc(server.file, "logs", name="sleeper", since=first["next"])
    assert later["lines"] == ["line 3", "line 4"] and later["next"] == 5

    nothing = await rpc(server.file, "logs", name="sleeper", since=later["next"])
    assert nothing["lines"] == [] and nothing["next"] == 5


async def test_cursor_counts_lines_the_buffer_has_dropped(server):
    proc = server.manager.processes["sleeper"]
    proc.output = type(proc.output)(maxlen=2)
    for i in range(5):
        proc.log(f"line {i}")
    result = await rpc(server.file, "logs", name="sleeper", since=0)
    assert result["lines"] == ["line 3", "line 4"] and result["next"] == 5


async def test_since_must_be_a_non_negative_integer(server):
    with pytest.raises(ApiError, match="'since' must be a non-negative integer"):
        await rpc(server.file, "logs", name="sleeper", since=-1)


async def test_sync_returns_states_and_raw_lines_past_each_cursor(server):
    server.manager.processes["sleeper"].log("\x1b[31mred\x1b[0m")
    result = await rpc(server.file, "sync")
    assert {e["name"] for e in result["entries"]} == {"sleeper", "job"}
    assert result["logs"]["sleeper"] == {"lines": ["\x1b[31mred\x1b[0m"], "next": 1}
    assert result["logs"]["job"] == {"lines": [], "next": 0}

    server.manager.processes["sleeper"].log("more")
    again = await rpc(server.file, "sync", cursors={"sleeper": 1})
    assert again["logs"]["sleeper"] == {"lines": ["more"], "next": 2}


async def test_sync_rejects_bad_cursors(server):
    with pytest.raises(ApiError, match="'cursors' must be an object"):
        await rpc(server.file, "sync", cursors=[1])
