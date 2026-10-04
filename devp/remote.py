"""A stand-in for `ProcessManager` that views and drives a devp running elsewhere.

`devp attach` (what the web UI runs for each browser session) builds the normal TUI on
top of a `RemoteManager`: processes live in the host devp, and this polls its control
API for their state and output and forwards start/stop/restart to it. Closing the
view leaves the host's processes running.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from devp.api import ApiError, call
from devp.config import Config
from devp.process import (
    MAX_BUFFER_LINES,
    ErrorCallback,
    OutputCallback,
    ProcessState,
    StateCallback,
)

_POLL_INTERVAL = 0.3
_MAX_FAILURES = 5  # consecutive failed polls before the host counts as gone


class _Reconfigured(Exception):
    """The host's process set changed under this manager."""


@dataclass(frozen=True)
class RemoteConfig:
    """The little of a process's config the UI reads."""

    name: str
    depends_on: list[str] = field(default_factory=list)
    has_ready_check: bool = False


class RemoteRunnable:
    """One process or cron job in the host devp, mirrored from its latest snapshot."""

    def __init__(self, manager: RemoteManager, info: dict[str, Any]) -> None:
        self._manager = manager
        self.config = RemoteConfig(info["name"])
        self.is_cron = info["kind"] == "cron"
        self.output: deque[str] = deque(maxlen=MAX_BUFFER_LINES)
        self.cursor: int | None = None  # next line to ask the host for
        self.apply(info)

    def apply(self, info: dict[str, Any]) -> None:
        """Take on the host's latest description of this entry."""
        self.state = ProcessState[info["state"].upper()]
        self.pid: int | None = info["pid"]
        self.exit_code: int | None = info["exit_code"]
        self.last_error: str | None = info["last_error"]
        self.start_count = info["restarts"] + 1
        self._uptime = info["uptime"]
        self._sampled_at = time.monotonic()
        next_run = info.get("next_run_at")
        self.next_run_at = datetime.fromisoformat(next_run) if next_run else None

    @property
    def uptime(self) -> float | None:
        if self._uptime is None:
            return None
        return self._uptime + (time.monotonic() - self._sampled_at)

    def log(self, line: str) -> None:
        self.output.append(line)

    async def start(self) -> None:
        await self._manager.request("start", name=self.config.name)

    async def stop(self) -> None:
        await self._manager.request("stop", name=self.config.name)

    async def restart(self) -> None:
        await self._manager.request("restart", name=self.config.name)


class RemoteManager:
    """The `ProcessManager` interface the app uses, backed by a host devp's control API."""

    def __init__(self, api_file: Path) -> None:
        self._api_file = api_file
        self.on_disconnect: Callable[[], object] | None = None
        self.on_reconfigured: Callable[[], object] | None = None  # the host reloaded its config
        self._on_output: OutputCallback | None = None
        self._on_state_change: StateCallback | None = None
        self._on_error: ErrorCallback | None = None
        # Fetched up front: the app lays out its sidebar before anything is polling.
        snapshot = call(api_file, "sync")
        self._epoch = snapshot["epoch"]
        self.config = Config(processes=[], crons=[])
        self.processes: dict[str, RemoteRunnable] = {
            info["name"]: RemoteRunnable(self, info) for info in snapshot["entries"]
        }
        self._apply_logs(snapshot["logs"], notify=False)

    async def request(self, method: str, **params: Any) -> Any:
        """Call the host; a failure is shown as an error toast rather than raised into the UI."""
        try:
            return await asyncio.to_thread(call, self._api_file, method, params)
        except ApiError as exc:
            if self._on_error is not None:
                self._on_error("devp", str(exc))
            return None

    def build(
        self,
        on_output: OutputCallback | None = None,
        on_state_change: StateCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self._on_output = on_output
        self._on_state_change = on_state_change
        self._on_error = on_error

    async def autostart(self) -> None:
        """Poll the host until cancelled (the app runs this as its background worker)."""
        failures = 0
        while True:
            try:
                await self._poll()
                failures = 0
            except _Reconfigured:
                return  # the owner replaces this manager with a fresh one
            except ApiError:
                failures += 1
                if failures >= _MAX_FAILURES:
                    if self.on_disconnect is not None:
                        self.on_disconnect()
                    return
            await asyncio.sleep(_POLL_INTERVAL)

    async def _poll(self) -> None:
        cursors = {n: r.cursor for n, r in self.processes.items() if r.cursor is not None}
        snapshot = await asyncio.to_thread(call, self._api_file, "sync", {"cursors": cursors})
        if snapshot["epoch"] != self._epoch:
            # The host swapped its processes for a reloaded config's: these are stale.
            if self.on_reconfigured is not None:
                self.on_reconfigured()
            raise _Reconfigured
        for info in snapshot["entries"]:
            runnable = self.processes.get(info["name"])
            if runnable is None:
                continue  # the host reloaded its config; reattach to see new entries
            before, error = runnable.state, runnable.last_error
            runnable.apply(info)
            if runnable.state != before and self._on_state_change is not None:
                self._on_state_change(runnable.config.name, runnable.state)
            if runnable.last_error and runnable.last_error != error and self._on_error is not None:
                self._on_error(runnable.config.name, runnable.last_error)
        self._apply_logs(snapshot["logs"], notify=True)

    def _apply_logs(self, logs: dict[str, Any], *, notify: bool) -> None:
        for name, entry in logs.items():
            runnable = self.processes.get(name)
            if runnable is None:
                continue
            runnable.cursor = entry["next"]
            for line in entry["lines"]:
                runnable.output.append(line)
                if notify and self._on_output is not None:
                    self._on_output(name, line)

    async def start_all(self) -> None:
        await self.request("start_all")

    async def stop_all(self) -> None:
        await self.request("stop_all")

    async def shutdown_all(self) -> None:
        """Leaving the view: the host's processes keep running."""
