import asyncio
import sys
from datetime import datetime

import pytest

from devp.config import CronConfig
from devp.cron import CronJob
from devp.process import ProcessState

pytestmark = pytest.mark.asyncio

# A schedule that (barring test flakiness around New Year's Day) never fires on its own
# during a test run, so scheduler-loop behavior can be tested via manual triggers.
_FAR_FUTURE_SCHEDULE = "0 0 1 1 *"


def python_command(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


async def test_enabled_job_starts_scheduled():
    config = CronConfig(
        name="job", command=python_command("print('hi')"), schedule=_FAR_FUTURE_SCHEDULE
    )
    job = CronJob(config)
    assert job.state == ProcessState.SCHEDULED


async def test_disabled_job_starts_stopped():
    config = CronConfig(
        name="job",
        command=python_command("print('hi')"),
        schedule=_FAR_FUTURE_SCHEDULE,
        enabled=False,
    )
    job = CronJob(config)
    assert job.state == ProcessState.STOPPED


async def test_manual_start_runs_and_returns_to_scheduled():
    config = CronConfig(
        name="job", command=python_command("print('hi')"), schedule=_FAR_FUTURE_SCHEDULE
    )
    job = CronJob(config)

    await job.start()
    await job._process.wait()

    assert "hi" in job.output
    assert job.state == ProcessState.SCHEDULED


async def test_failed_run_reports_crashed():
    config = CronConfig(
        name="job",
        command=python_command("import sys; sys.exit(3)"),
        schedule=_FAR_FUTURE_SCHEDULE,
    )
    job = CronJob(config)

    await job.start()
    await job._process.wait()

    assert job.state == ProcessState.CRASHED
    assert job.exit_code == 3
    assert job.last_error == "exited with code 3"


async def test_stop_terminates_a_running_manual_invocation():
    config = CronConfig(
        name="job",
        command=python_command("import time; time.sleep(30)"),
        schedule=_FAR_FUTURE_SCHEDULE,
    )
    job = CronJob(config)

    await job.start()
    assert job.state == ProcessState.RUNNING

    await job.stop()
    assert job.state == ProcessState.SCHEDULED


async def test_schedule_loop_triggers_runs_and_can_be_stopped():
    config = CronConfig(
        name="job", command=python_command("print('tick')"), schedule="* * * * *"
    )
    job = CronJob(config)
    # Fire immediately instead of waiting for the real per-minute boundary.
    job._compute_next_run = datetime.now

    await job.start_schedule()
    try:
        for _ in range(50):
            if "tick" in job.output:
                break
            await asyncio.sleep(0.05)
        assert "tick" in job.output
    finally:
        await job.stop_schedule()
        await job.stop()


async def test_state_change_callback_fires_with_translated_state():
    seen: list[ProcessState] = []
    config = CronConfig(
        name="job", command=python_command("print('hi')"), schedule=_FAR_FUTURE_SCHEDULE
    )
    job = CronJob(config, on_state_change=lambda name, state: seen.append(state))

    await job.start()
    await job._process.wait()

    assert ProcessState.RUNNING in seen
    assert seen[-1] == ProcessState.SCHEDULED
