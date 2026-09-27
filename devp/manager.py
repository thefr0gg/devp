"""Builds and coordinates every configured process and cron job, respecting `depends_on`."""

from __future__ import annotations

import asyncio

from devp.config import Config
from devp.cron import CronJob
from devp.process import ErrorCallback, ManagedProcess, OutputCallback, StateCallback

Runnable = ManagedProcess | CronJob


class ProcessManager:
    """Owns every configured `ManagedProcess` and `CronJob`, keyed by name."""

    def __init__(self, config: Config) -> None:
        self.processes: dict[str, Runnable] = {}
        self._process_configs = config.processes
        self._cron_configs = config.crons

    def build(
        self,
        on_output: OutputCallback | None = None,
        on_state_change: StateCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        """Create a `ManagedProcess`/`CronJob` for each configured entry, wired to the callbacks."""
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

        Config validation already guarantees every `depends_on` reference exists, is
        itself autostarted, and forms no cycle, so this can simply wait on a per-name
        event rather than compute an explicit topological order.
        """
        started = {name: asyncio.Event() for name in self.processes}

        async def start_one(name: str) -> None:
            runnable = self.processes[name]
            for dep in runnable.config.depends_on:
                await started[dep].wait()
            try:
                if isinstance(runnable, CronJob):
                    await runnable.start_schedule()
                elif runnable.config.autostart:
                    await runnable.start()
            finally:
                started[name].set()

        await asyncio.gather(*(start_one(name) for name in self.processes))

    async def shutdown_all(self) -> None:
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
