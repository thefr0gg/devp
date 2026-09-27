"""Recurring cron-style jobs: run a command on a schedule, reusing ManagedProcess for execution."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime

from croniter import croniter

from devp.config import CronConfig, ProcessConfig
from devp.process import ErrorCallback, ManagedProcess, OutputCallback, ProcessState, StateCallback


class CronJob:
    """Runs `config.command` on `config.schedule`.

    Exposes the same async `start`/`stop`/`restart` interface as `ManagedProcess` so the
    TUI can control either kind of runnable without knowing which one it has.
    """

    def __init__(
        self,
        config: CronConfig,
        on_output: OutputCallback | None = None,
        on_state_change: StateCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.config = config
        self.enabled = config.enabled
        self.next_run_at: datetime | None = None
        self._on_state_change = on_state_change
        self._process = ManagedProcess(
            ProcessConfig(
                name=config.name,
                command=config.command,
                cwd=config.cwd,
                env=config.env,
                autostart=False,
            ),
            on_output=on_output,
            on_state_change=self._handle_inner_state_change,
            on_error=on_error,
        )
        self._scheduler_task: asyncio.Task[None] | None = None

    @property
    def output(self) -> deque[str]:
        return self._process.output

    @property
    def exit_code(self) -> int | None:
        return self._process.exit_code

    @property
    def last_error(self) -> str | None:
        return self._process.last_error

    @property
    def state(self) -> ProcessState:
        """RUNNING/CRASHED pass through; otherwise SCHEDULED if enabled, else STOPPED."""
        inner = self._process.state
        if inner in (ProcessState.RUNNING, ProcessState.CRASHED):
            return inner
        return ProcessState.SCHEDULED if self.enabled else ProcessState.STOPPED

    def _handle_inner_state_change(self, _name: str, _inner_state: ProcessState) -> None:
        """Re-emit the wrapped process's state change as this job's own (translated) state."""
        self._notify_state_change()

    def _notify_state_change(self) -> None:
        if self._on_state_change is not None:
            self._on_state_change(self.config.name, self.state)

    def _compute_next_run(self) -> datetime:
        return croniter(self.config.schedule, datetime.now()).get_next(datetime)

    async def start_schedule(self) -> None:
        """Begin the recurring schedule loop, if enabled and not already running."""
        if not self.enabled or self._scheduler_task is not None:
            return
        self._scheduler_task = asyncio.create_task(self._run_loop())

    async def stop_schedule(self) -> None:
        """Cancel the recurring schedule loop, without touching a run already in progress."""
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            self._scheduler_task = None

    async def _run_loop(self) -> None:
        """Sleep until each scheduled time, run the command, and wait for it before rescheduling.

        A tick that arrives while a previous (or manually triggered) run is still in
        progress is skipped, since `ManagedProcess.start()` no-ops while RUNNING.
        """
        while self.enabled:
            self.next_run_at = self._compute_next_run()
            self._notify_state_change()
            delay = max(0.0, (self.next_run_at - datetime.now()).total_seconds())
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

            if not self.enabled:
                return

            self._process.log(f"--- scheduled run starting at {datetime.now():%H:%M:%S} ---")
            await self._process.start()
            try:
                # Shielded so cancelling the schedule loop (stop_schedule) can't also
                # cancel the process's own exit-tracking task and corrupt its state.
                await asyncio.shield(self._process.wait())
            except asyncio.CancelledError:
                return

    async def start(self) -> None:
        """Manually trigger a run right now, independent of the schedule."""
        if self._process.state != ProcessState.RUNNING:
            self._process.log(f"--- manual run starting at {datetime.now():%H:%M:%S} ---")
        await self._process.start()

    async def stop(self) -> None:
        """Stop the run currently in progress, if any."""
        await self._process.stop()

    async def restart(self) -> None:
        """Stop the current run (if any) and immediately run again."""
        await self._process.restart()
