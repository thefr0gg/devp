import sys

import pytest

from devp.config import Config, CronConfig, ProcessConfig
from devp.cron import CronJob
from devp.manager import ProcessManager
from devp.process import ManagedProcess, ProcessState

pytestmark = pytest.mark.asyncio


def python_command(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


async def test_build_creates_both_processes_and_cron_jobs():
    config = Config(
        processes=[ProcessConfig(name="api", command=python_command("print('hi')"))],
        crons=[
            CronConfig(
                name="backup",
                command=python_command("print('hi')"),
                schedule="0 0 1 1 *",
            )
        ],
    )
    manager = ProcessManager(config)
    manager.build()

    assert isinstance(manager.processes["api"], ManagedProcess)
    assert isinstance(manager.processes["backup"], CronJob)


async def test_autostart_starts_processes_and_schedules_cron_jobs():
    config = Config(
        processes=[
            ProcessConfig(name="api", command=python_command("import time; time.sleep(30)")),
            ProcessConfig(
                name="idle", command=python_command("print('hi')"), autostart=False
            ),
        ],
        crons=[
            CronConfig(
                name="backup",
                command=python_command("print('hi')"),
                schedule="0 0 1 1 *",
            )
        ],
    )
    manager = ProcessManager(config)
    manager.build()

    await manager.autostart()

    assert manager.processes["api"].state == ProcessState.RUNNING
    assert manager.processes["idle"].state == ProcessState.STOPPED
    assert manager.processes["backup"].state == ProcessState.SCHEDULED
    assert manager.processes["backup"]._scheduler_task is not None

    await manager.shutdown_all()


async def test_shutdown_all_stops_processes_and_cancels_cron_schedules():
    config = Config(
        processes=[
            ProcessConfig(name="api", command=python_command("import time; time.sleep(30)"))
        ],
        crons=[
            CronConfig(
                name="backup",
                command=python_command("print('hi')"),
                schedule="0 0 1 1 *",
            )
        ],
    )
    manager = ProcessManager(config)
    manager.build()
    await manager.autostart()

    await manager.shutdown_all()

    assert manager.processes["api"].state == ProcessState.STOPPED
    assert manager.processes["backup"]._scheduler_task is None


async def test_autostart_starts_dependencies_before_dependents():
    config = Config(
        processes=[
            ProcessConfig(name="db", command=python_command("print('hi')")),
            ProcessConfig(
                name="api", command=python_command("print('hi')"), depends_on=["db"]
            ),
        ],
        crons=[],
    )
    manager = ProcessManager(config)
    manager.build()

    call_order: list[str] = []
    for name in ("db", "api"):
        original = manager.processes[name].start

        async def wrapped(original=original, name=name):
            call_order.append(name)
            await original()

        manager.processes[name].start = wrapped

    await manager.autostart()

    assert call_order == ["db", "api"]
    await manager.shutdown_all()


async def test_shutdown_stops_dependents_before_dependencies():
    config = Config(
        processes=[
            ProcessConfig(
                name="db", command=python_command("import time; time.sleep(30)")
            ),
            ProcessConfig(
                name="api",
                command=python_command("import time; time.sleep(30)"),
                depends_on=["db"],
            ),
        ],
        crons=[],
    )
    manager = ProcessManager(config)
    manager.build()
    await manager.autostart()

    call_order: list[str] = []
    for name in ("db", "api"):
        original = manager.processes[name].stop

        async def wrapped(original=original, name=name):
            call_order.append(name)
            await original()

        manager.processes[name].stop = wrapped

    await manager.shutdown_all()

    assert call_order == ["api", "db"]
