"""Builds and coordinates every configured process and cron job, respecting `depends_on`."""

from __future__ import annotations

import asyncio

from devp.config import Config
from devp.cron import CronJob
from devp.process import (
    ErrorCallback,
    ManagedProcess,
    OutputCallback,
    ProcessState,
    StateCallback,
)
from devp.watch import FileWatcher

Runnable = ManagedProcess | CronJob


class ProcessManager:
    """Owns every configured `ManagedProcess` and `CronJob`, keyed by name."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.processes: dict[str, Runnable] = {}
        self._process_configs = config.processes
        self._cron_configs = config.crons
        self._on_error: ErrorCallback | None = None
        self._watchers: list[FileWatcher] = []

    def build(
        self,
        on_output: OutputCallback | None = None,
        on_state_change: StateCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        """Create a `ManagedProcess`/`CronJob` for each configured entry, wired to the callbacks."""
        self._on_error = on_error
        for process_config in self._process_configs:
            self.processes[process_config.name] = ManagedProcess(
                process_config,
                on_output=on_output,
                on_state_change=on_state_change,
                on_error=on_error,
            )
        for cron_config in self._cron_configs:
            self.processes[cron_config.name] = CronJob(
                cron_config,
                on_output=on_output,
                on_state_change=on_state_change,
                on_error=on_error,
            )

    async def autostart(self) -> None:
        """Start every autostart process and cron schedule, waiting for `depends_on` first.

        A dependency counts as started once it's *ready*: immediately after spawning,
        or, with `ready_when`/`ready_port`, once that check passes. If a dependency
        exits first or its check times out, its dependents are left stopped (with a
        note in their log) rather than started against something that isn't up.

        Config validation already guarantees every `depends_on` reference exists, is
        itself autostarted, and forms no cycle, so this can simply wait on a per-name
        event rather than compute an explicit topological order.
        """
        self.start_watchers()
        await self._start_in_dependency_order(include_manual=False)

    async def start_all(self) -> None:
        """Start every process (autostart or not) and cron schedule, in dependency order."""
        await self._start_in_dependency_order(include_manual=True)

    async def _start_in_dependency_order(self, *, include_manual: bool) -> None:
        settled = {name: asyncio.Event() for name in self.processes}
        ready: dict[str, bool] = {}

        async def start_one(name: str) -> None:
            runnable = self.processes[name]
            ready[name] = False
            try:
                for dep in runnable.config.depends_on:
                    await settled[dep].wait()
                not_ready = [dep for dep in runnable.config.depends_on if not ready[dep]]
                if not_ready:
                    self._skip_start(runnable, not_ready)
                elif isinstance(runnable, CronJob):
                    await runnable.start_schedule()
                    ready[name] = True
                elif runnable.config.autostart or include_manual:
                    await runnable.start()
                    ready[name] = await runnable.wait_ready()
            finally:
                settled[name].set()

        await asyncio.gather(*(start_one(name) for name in self.processes))

    def start_watchers(self) -> None:
        """Watch each process's `watch` globs, restarting it when a matching file changes."""
        if self._watchers:
            return
        for runnable in self.processes.values():
            if isinstance(runnable, ManagedProcess) and runnable.config.watch:
                watcher = FileWatcher(
                    runnable.config.watch,
                    runnable.config.cwd or ".",
                    lambda changed, process=runnable: self._restart_on_change(process, changed),
                )
                watcher.start()
                self._watchers.append(watcher)

    async def _restart_on_change(self, process: ManagedProcess, changed: list[str]) -> None:
        # A process the user stopped stays stopped; a crashed one gets another try.
        active = (ProcessState.STARTING, ProcessState.RUNNING, ProcessState.CRASHED)
        if process.state not in active:
            return
        what = changed[0] if len(changed) == 1 else f"{len(changed)} files ({changed[0]}, ...)"
        process.log(f"--- {what} changed, restarting ---")
        await process.restart()

    def _skip_start(self, runnable: Runnable, not_ready: list[str]) -> None:
        deps = ", ".join(f"'{dep}'" for dep in not_ready)
        reason = f"not started: {deps} didn't become ready"
        runnable.log(f"--- {reason} (press s to start it anyway) ---")
        if self._on_error is not None:
            self._on_error(runnable.config.name, reason)

    async def shutdown_all(self) -> None:
        """Stop file watching, then every process/run and cron schedule (see `stop_all`)."""
        for watcher in self._watchers:
            await watcher.stop()
        self._watchers.clear()
        await self.stop_all()

    async def stop_all(self) -> None:
        """Stop every process/run and cron schedule, stopping dependents before their dependencies."""
        for runnable in self.processes.values():
            if isinstance(runnable, CronJob):
                await runnable.stop_schedule()

        dependents: dict[str, list[str]] = {name: [] for name in self.processes}
        for name, runnable in self.processes.items():
            for dep in runnable.config.depends_on:
                dependents[dep].append(name)

        stopped = {name: asyncio.Event() for name in self.processes}

        async def stop_one(name: str) -> None:
            for dependent in dependents[name]:
                await stopped[dependent].wait()
            try:
                await self.processes[name].stop()
            finally:
                stopped[name].set()

        await asyncio.gather(
            *(stop_one(name) for name in self.processes),
            return_exceptions=True,
        )
