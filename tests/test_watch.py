import asyncio
import os
import sys

import pytest

from devp.config import Config, ProcessConfig
from devp.manager import ProcessManager
from devp.process import ProcessState
from devp.watch import FileWatcher, _glob_to_regex


def python_command(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("src/**/*.py", "src/app.py", True),
        ("src/**/*.py", "src/pkg/deep/mod.py", True),
        ("src/**/*.py", "src/app.js", False),
        ("src/**/*.py", "tests/app.py", False),
        ("*.toml", "devp.toml", True),
        ("*.toml", "conf/devp.toml", False),
        ("**/*.toml", "conf/devp.toml", True),
        ("config/?.yaml", "config/a.yaml", True),
        ("config/?.yaml", "config/ab.yaml", False),
        ("app.py", "app.py", True),
        ("app.py", "old_app.py", False),
    ],
)
def test_glob_patterns(pattern, path, matches):
    assert bool(_glob_to_regex(pattern).match(path)) is matches


async def _noop(changed):
    pass


def test_scan_skips_ignored_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.py").write_text("x")
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "app.py").write_text("x")

    watcher = FileWatcher(["**/*.py"], tmp_path, _noop)
    assert set(watcher.scan()) == {"src/app.py"}


@pytest.mark.asyncio
async def test_reports_modified_added_and_removed_files(tmp_path):
    (tmp_path / "a.py").write_text("1")
    (tmp_path / "b.py").write_text("1")
    batches: list[list[str]] = []

    async def on_change(changed):
        batches.append(changed)

    watcher = FileWatcher(["*.py"], tmp_path, on_change, interval=0.05)
    watcher.start()
    await asyncio.sleep(0.1)

    (tmp_path / "a.py").write_text("changed, and longer")
    (tmp_path / "c.py").write_text("new")
    os.remove(tmp_path / "b.py")
    (tmp_path / "notes.txt").write_text("not watched")
    for _ in range(40):
        if batches:
            break
        await asyncio.sleep(0.05)
    await watcher.stop()

    assert batches and batches[0] == ["a.py", "b.py", "c.py"]


@pytest.mark.asyncio
async def test_manager_restarts_running_process_on_change_but_not_a_stopped_one(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("devp.watch._POLL_INTERVAL", 0.05)
    (tmp_path / "app.py").write_text("v1")
    config = Config(
        processes=[
            ProcessConfig(
                name="api",
                command=python_command("import time; time.sleep(30)"),
                cwd=str(tmp_path),
                watch=["*.py"],
            ),
            ProcessConfig(
                name="idle",
                command=python_command("import time; time.sleep(30)"),
                cwd=str(tmp_path),
                watch=["*.py"],
                autostart=False,
            ),
        ],
        crons=[],
    )
    manager = ProcessManager(config)
    manager.build()
    await manager.autostart()
    api, idle = manager.processes["api"], manager.processes["idle"]
    first_pid = api._proc.pid
    await asyncio.sleep(0.2)

    (tmp_path / "app.py").write_text("v2 with more bytes")
    for _ in range(60):
        if api._proc.pid != first_pid and api.state == ProcessState.RUNNING:
            break
        await asyncio.sleep(0.05)

    assert api._proc.pid != first_pid
    assert "--- app.py changed, restarting ---" in api.output
    assert idle.state == ProcessState.STOPPED

    await manager.shutdown_all()
    assert not manager._watchers
