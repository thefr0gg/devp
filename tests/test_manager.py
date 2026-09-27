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


async def test_dependents_wait_for_a_dependency_to_be_ready():
    import asyncio

    config = Config(
        processes=[
            ProcessConfig(
                name="db",
                command=python_command(
                    "import time; time.sleep(0.4); print('ready to accept connections'); "
                    "time.sleep(30)"
                ),
                ready_when="ready to accept",
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
    events = []
    manager.build(on_state_change=lambda name, state: events.append((name, state)))

    await asyncio.wait_for(manager.autostart(), 5)
    assert events.index(("db", ProcessState.RUNNING)) < events.index(("api", ProcessState.RUNNING))
    assert ("db", ProcessState.STARTING) in events

    await manager.shutdown_all()


async def test_dependents_are_not_started_when_a_dependency_never_becomes_ready():
    import asyncio

    config = Config(
        processes=[
            ProcessConfig(
                name="db", command=python_command("import sys; sys.exit(1)"), ready_when="ready"
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
    errors = []
    manager.build(on_error=lambda name, text: errors.append((name, text)))

    await asyncio.wait_for(manager.autostart(), 5)
    api = manager.processes["api"]
    assert api.state == ProcessState.STOPPED
    assert ("api", "not started: 'db' didn't become ready") in errors
    assert any("not started" in line for line in api.output)

    await manager.shutdown_all()


async def test_concurrent_starts_spawn_only_once(monkeypatch):
    import asyncio

    spawned = []
    real_exec = asyncio.create_subprocess_exec

    async def counting_exec(*args, **kwargs):
        spawned.append(args)
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", counting_exec)
    proc = ManagedProcess(
        ProcessConfig(name="api", command=python_command("import time; time.sleep(30)"))
    )
    await asyncio.gather(proc.start(), proc.start(), proc.start())
    assert len(spawned) == 1 and proc.state == ProcessState.RUNNING
    await proc.stop()


async def test_stop_all_pauses_cron_schedules_and_start_all_resumes_them():
    config = Config(
        processes=[],
        crons=[CronConfig(name="backup", command=python_command("pass"), schedule="0 0 1 1 *")],
    )
    manager = ProcessManager(config)
    manager.build()
    job = manager.processes["backup"]
    await manager.autostart()
    assert job.state == ProcessState.SCHEDULED

    await manager.stop_all()
    assert job.state == ProcessState.STOPPED

    await manager.start_all()
    assert job.state == ProcessState.SCHEDULED
    await manager.shutdown_all()
