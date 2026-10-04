import json
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

from devp.api import API_FILE_NAME
from devp.config import Config, ProcessConfig
from devp.web import WebHost, attach_command

pytestmark = pytest.mark.asyncio


def make_host(tmp_path) -> WebHost:
    config = Config(
        processes=[
            ProcessConfig(name="p", command=[sys.executable, "-c", "pass"], autostart=False)
        ],
        crons=[],
    )
    return WebHost(config, tmp_path / "devp.toml", "localhost", 0)


async def test_pages_need_the_token_and_it_is_remembered_in_a_cookie(tmp_path):
    host = make_host(tmp_path)
    async with TestClient(TestServer(await host._make_app())) as client:
        assert (await client.get("/")).status == 403
        assert (await client.get("/?token=wrong")).status == 403
        assert (await client.get("/static/js/textual.js")).status == 403

        ok = await client.get(f"/?token={host.token}")
        assert ok.status == 200
        # The cookie now authorizes the page's own follow-up requests (assets, websocket).
        assert (await client.get("/static/js/textual.js")).status == 200


async def test_the_session_command_attaches_to_this_hosts_api(tmp_path):
    host = make_host(tmp_path)
    assert host.api_file == tmp_path / API_FILE_NAME
    command = attach_command(host.api_file)
    assert "devp" in command and "attach" in command and API_FILE_NAME in command
    assert host.command == command


async def test_startup_serves_the_api_and_shutdown_cleans_up(tmp_path):
    host = make_host(tmp_path)
    app = await host._make_app()
    await host.on_startup(app)
    try:
        assert host.api_file.exists()
        assert host.url.startswith("http://localhost:0/?token=")
    finally:
        await host.on_shutdown(app)
    assert not host.api_file.exists()


def write_config(path, *names, comment=""):
    body = "".join(
        f'[[process]]\nname = "{name}"\n'
        f'command = [{json.dumps(sys.executable)}, "-c", "import time; time.sleep(30)"]\n'
        for name in names
    )
    path.write_text(comment + body)


async def running(manager, name):
    import asyncio

    from devp.process import ProcessState

    for _ in range(100):
        if name in manager.processes and manager.processes[name].state == ProcessState.RUNNING:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{name} never started")


async def started_host(tmp_path, *names):
    from devp.config import load_config

    config_path = tmp_path / "devp.toml"
    write_config(config_path, *names)
    host = WebHost(load_config(config_path), config_path, "localhost", 0)
    app = await host._make_app()
    await host.on_startup(app)
    return host, app


async def test_reload_replaces_the_processes_with_the_new_configs(tmp_path):
    from devp.process import ProcessState

    host, app = await started_host(tmp_path, "a")
    try:
        await running(host.manager, "a")
        old_manager, old_proc = host.manager, host.manager.processes["a"]
        assert old_proc.state == ProcessState.RUNNING

        write_config(host.config_path, "a", "b")
        assert await host.reload_config() is True

        assert host.manager is not old_manager
        assert old_proc.state == ProcessState.STOPPED  # the old set was shut down
        assert set(host.manager.processes) == {"a", "b"}
        await running(host.manager, "b")
        assert host.manager.processes["b"].state == ProcessState.RUNNING
    finally:
        await host.on_shutdown(app)


async def test_reload_ignores_a_save_that_changes_nothing_meaningful(tmp_path):
    host, app = await started_host(tmp_path, "a")
    try:
        manager = host.manager
        write_config(host.config_path, "a", comment="# just a comment\n")
        assert await host.reload_config() is False
        assert host.manager is manager
    finally:
        await host.on_shutdown(app)


async def test_reload_keeps_running_when_the_new_config_is_invalid(tmp_path, capsys):
    from devp.process import ProcessState

    host, app = await started_host(tmp_path, "a")
    try:
        await running(host.manager, "a")
        manager = host.manager
        host.config_path.write_text("[[process]]\nname = \n")
        assert await host.reload_config() is False
        assert host.manager is manager
        assert manager.processes["a"].state == ProcessState.RUNNING
    finally:
        await host.on_shutdown(app)


async def test_saving_the_config_triggers_a_reload_by_itself(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr("devp.web._CONFIG_POLL_INTERVAL", 0.05)
    host, app = await started_host(tmp_path, "a")
    try:
        write_config(host.config_path, "a", "b")
        for _ in range(100):
            if "b" in host.manager.processes:
                break
            await asyncio.sleep(0.05)
        assert "b" in host.manager.processes
    finally:
        await host.on_shutdown(app)


async def test_startup_prints_devps_banner_not_textual_serves(tmp_path, capsys):
    host, app = await started_host(tmp_path, "a")
    try:
        out = capsys.readouterr().out
        assert "devp" in out and host.url in out
        assert "Serving" not in out  # textual-serve's logo and command echo are gone
    finally:
        await host.on_shutdown(app)


async def test_blocked_requests_are_logged_not_dumped_as_http_access_lines(tmp_path, capsys):
    host = make_host(tmp_path)
    async with TestClient(TestServer(await host._make_app())) as client:
        capsys.readouterr()
        assert (await client.get("/")).status == 403
    out = capsys.readouterr().out
    assert "auth" in out and "blocked a request" in out
    assert "GET /" not in out


async def test_process_events_and_reloads_are_logged(tmp_path, capsys):
    host, app = await started_host(tmp_path, "a")
    try:
        await running(host.manager, "a")
        write_config(host.config_path, "a", "b")
        await host.reload_config()
        await running(host.manager, "b")
        out = capsys.readouterr().out
        assert "running" in out
        assert "devp.toml changed" in out and "reloaded" in out
        assert out.index("changed") < out.index("reloaded")
    finally:
        await host.on_shutdown(app)


async def test_session_processes_have_their_pipes_closed_when_they_stop():
    from types import SimpleNamespace
    from unittest.mock import Mock

    import textual_serve.server as textual_serve_server

    from devp.web import _TidyAppService

    # textual-serve builds each session's app process from this name.
    assert textual_serve_server.AppService is _TidyAppService

    transport = Mock()
    service = object.__new__(_TidyAppService)
    service._task = None  # nothing running: the library's own stop() returns at once
    service._process = SimpleNamespace(_transport=transport)

    await service.stop()
    transport.close.assert_called_once()

    service._process = None  # never started, or already cleaned up: must not raise
    await service.stop()
