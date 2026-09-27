import sys

import pytest

from devp.config import ProcessConfig
from devp.process import ManagedProcess, ProcessState

pytestmark = pytest.mark.asyncio


def python_command(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


async def test_start_runs_process_and_captures_output():
    config = ProcessConfig(name="ticker", command=python_command("print('hello')"))
    proc = ManagedProcess(config)

    await proc.start()
    assert proc.state == ProcessState.RUNNING

    await proc._wait_task
    assert proc.state == ProcessState.STOPPED
    assert "hello" in proc.output


async def test_stop_terminates_running_process():
    config = ProcessConfig(
        name="sleeper", command=python_command("import time; time.sleep(30)")
    )
    proc = ManagedProcess(config)

    await proc.start()
    assert proc.state == ProcessState.RUNNING

    await proc.stop()
    assert proc.state == ProcessState.STOPPED
    assert proc._proc.returncode is not None


async def test_restart_produces_a_new_process():
    config = ProcessConfig(
        name="sleeper", command=python_command("import time; time.sleep(30)")
    )
    proc = ManagedProcess(config)

    await proc.start()
    first_pid = proc._proc.pid

    await proc.restart()
    assert proc.state == ProcessState.RUNNING
    assert proc._proc.pid != first_pid

    await proc.stop()


async def test_nonzero_exit_marks_crashed():
    config = ProcessConfig(name="failer", command=python_command("import sys; sys.exit(1)"))
    proc = ManagedProcess(config)

    await proc.start()
    await proc._wait_task

    assert proc.state == ProcessState.CRASHED


async def test_output_buffer_respects_maxlen():
    config = ProcessConfig(
        name="chatty",
        command=python_command("[print(i) for i in range(20)]"),
    )
    proc = ManagedProcess(config)
    proc.output = proc.output.__class__(maxlen=5)

    await proc.start()
    await proc._wait_task

    assert len(proc.output) == 5
    assert list(proc.output) == [str(i) for i in range(15, 20)]
