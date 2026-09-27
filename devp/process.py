"""Spawning, monitoring, and controlling the subprocesses devp manages."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable
from enum import Enum, auto

from devp.ansi import strip_ansi
from devp.config import ProcessConfig

_IS_WINDOWS = sys.platform == "win32"
MAX_BUFFER_LINES = 5000
_STOP_TIMEOUT = 5.0
_BASE_RESTART_DELAY = 1.0
_MAX_RESTART_DELAY = 30.0
# A crash after at least this much uptime starts the autorestart backoff over, so a
# process that crashes once in a long while isn't treated like a crash loop.
STABLE_UPTIME = 30.0
_PORT_POLL_INTERVAL = 0.25

OutputCallback = Callable[[str, str], None]
StateCallback = Callable[[str, "ProcessState"], None]
ErrorCallback = Callable[[str, str], None]


def _has_console() -> bool:
    """Whether this process has a real Windows console attached.

    CTRL_BREAK_EVENT can only be delivered through a console; some hosts (certain
    task runners, detached services) have none, in which case there's no point
    attempting it before falling back to a hard kill.
    """
    if not _IS_WINDOWS:
        return False
    import ctypes

    return bool(ctypes.windll.kernel32.GetConsoleWindow())


class ProcessState(Enum):
    """Lifecycle states of a single managed process or cron job."""

    STOPPED = auto()
    STARTING = auto()  # spawned, waiting for its ready_when / ready_port check to pass
    RUNNING = auto()
    STOPPING = auto()
    CRASHED = auto()
    SCHEDULED = auto()  # cron job: enabled and idle, waiting for its next scheduled run


class ManagedProcess:
    """Owns one configured process: spawning it, streaming its output, and stopping it."""

    def __init__(
        self,
        config: ProcessConfig,
        on_output: OutputCallback | None = None,
        on_state_change: StateCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.config = config
        self.on_output = on_output
        self.on_state_change = on_state_change
        self.on_error = on_error
        self.state = ProcessState.STOPPED
        self.exit_code: int | None = None
        self.last_error: str | None = None
        self.output: deque[str] = deque(maxlen=MAX_BUFFER_LINES)
        self._proc: asyncio.subprocess.Process | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[None] | None = None
        self._restart_task: asyncio.Task[None] | None = None
        self._crash_count = 0
        self._started_at = 0.0
        self._ready: asyncio.Future[bool] | None = None
        self._start_lock = asyncio.Lock()
        self.start_count = 0  # successful spawns, so restarts = start_count - 1
        self._ready_task: asyncio.Task[None] | None = None
        self._ready_pattern = re.compile(config.ready_when) if config.ready_when else None

    def _set_state(self, state: ProcessState) -> None:
        """Update state and notify `on_state_change`, if set."""
        self.state = state
        if self.on_state_change is not None:
            self.on_state_change(self.config.name, state)

    def _report_error(self, message: str) -> None:
        """Record `message` as the last error and notify `on_error`, if set."""
        self.last_error = message
        if self.on_error is not None:
            self.on_error(self.config.name, message)

    def log(self, line: str) -> None:
        """Append an arbitrary line (e.g. a run separator) to the buffer without spawning anything."""
        self.output.append(line)
        if self.on_output is not None:
            self.on_output(self.config.name, line)

    async def wait(self) -> None:
        """Wait for the current run to finish, if one is in progress."""
        if self._wait_task is not None:
            await self._wait_task

    async def start(self) -> None:
        """Spawn the process if not already running.

        A string `command` runs through the shell; a list runs directly with no shell.
        A failure to spawn (e.g. missing executable) is reported as a CRASHED state
        rather than raised, so callers don't need to guard every call site.
        """
        # The state only changes once the spawn completes, so without the lock two
        # overlapping calls (e.g. autostart and "start all") could both spawn.
        async with self._start_lock:
            await self._start()

    async def _start(self) -> None:
        if self.state in (ProcessState.RUNNING, ProcessState.STARTING):
            return

        if self._restart_task is not None:
            self._restart_task.cancel()
            self._restart_task = None

        env = {**os.environ, **self.config.env}
        popen_kwargs: dict[str, object] = {
            "cwd": self.config.cwd,
            "env": env,
            # Never let children inherit the TUI's console stdin: they would steal
            # keystrokes and, on Windows, flip the shared console into echo/line mode
            # so typed keys and mouse escape sequences get painted over the UI.
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        try:
            if isinstance(self.config.command, str):
                self._proc = await asyncio.create_subprocess_shell(
                    self.config.command, **popen_kwargs
                )
            else:
                self._proc = await asyncio.create_subprocess_exec(
                    *self.config.command, **popen_kwargs
                )
        except OSError as exc:
            self._ready = asyncio.get_running_loop().create_future()
            self._ready.set_result(False)
            self._set_state(ProcessState.CRASHED)
            self._report_error(f"failed to start: {exc}")
            return

        self.exit_code = None
        self.start_count += 1
        self._started_at = time.monotonic()
        self._ready = asyncio.get_running_loop().create_future()
        if self.config.has_ready_check:
            self._set_state(ProcessState.STARTING)
            self._ready_task = asyncio.create_task(self._check_readiness())
        else:
            self._ready.set_result(True)
            self._set_state(ProcessState.RUNNING)
        self._pump_task = asyncio.create_task(self._pump_output())
        self._wait_task = asyncio.create_task(self._await_exit())

    @property
    def pid(self) -> int | None:
        """The OS process id of the current run, while one is alive."""
        if self._proc is None or self.state not in (ProcessState.STARTING, ProcessState.RUNNING):
            return None
        return self._proc.pid

    @property
    def uptime(self) -> float | None:
        """Seconds since the current run started, while one is alive."""
        if self.pid is None:
            return None
        return time.monotonic() - self._started_at

    async def wait_ready(self) -> bool:
        """Wait until the current run is ready to serve its dependents.

        A process without a `ready_when`/`ready_port` check is ready as soon as it
        spawns. Returns False if the run failed to start, exited before becoming
        ready, or didn't pass its check within `ready_timeout`.
        """
        if self._ready is None:
            return False
        return await asyncio.shield(self._ready)

    def _settle_ready(self, ready: bool) -> None:
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(ready)

    def _mark_ready(self) -> None:
        """The readiness check passed: move from STARTING to RUNNING."""
        if self.state == ProcessState.STARTING:
            self._settle_ready(True)
            self._set_state(ProcessState.RUNNING)

    async def _check_readiness(self) -> None:
        """Poll `ready_port` (or just wait for `ready_when` to match), up to `ready_timeout`."""
        assert self._ready is not None
        timeout = self.config.ready_timeout
        try:
            if self.config.ready_port is not None:
                await asyncio.wait_for(self._wait_for_port(self.config.ready_port), timeout)
                self._mark_ready()
            else:
                await asyncio.wait_for(asyncio.shield(self._ready), timeout)
        except asyncio.TimeoutError:
            if self.state == ProcessState.STARTING:
                self.log(f"--- no ready signal after {timeout:g}s ---")
                self._settle_ready(False)
                self._set_state(ProcessState.RUNNING)
                self._report_error(f"no ready signal after {timeout:g}s")
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _wait_for_port(port: int) -> None:
        while True:
            try:
                _, writer = await asyncio.open_connection("localhost", port)
            except OSError:
                await asyncio.sleep(_PORT_POLL_INTERVAL)
                continue
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            return

    async def _pump_output(self) -> None:
        """Read the process's merged stdout/stderr line by line into the scrollback buffer."""
        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            async for raw_line in self._proc.stdout:
                line = raw_line.decode(errors="replace").rstrip("\r\n")
                self.output.append(line)
                if self.on_output is not None:
                    self.on_output(self.config.name, line)
                if (
                    self._ready_pattern is not None
                    and self.state == ProcessState.STARTING
                    and self._ready_pattern.search(strip_ansi(line))
                ):
                    self._mark_ready()
        except asyncio.CancelledError:
            pass

    async def _await_exit(self) -> None:
        """Wait for the process to exit and set its final state (STOPPED or CRASHED)."""
        assert self._proc is not None
        returncode = await self._proc.wait()
        self.exit_code = returncode
        self._settle_ready(False)  # exited before its readiness check passed
        if self._ready_task is not None:
            self._ready_task.cancel()
            self._ready_task = None
        if self.state == ProcessState.STOPPING:
            self._set_state(ProcessState.STOPPED)
        elif returncode == 0:
            self._crash_count = 0
            self._set_state(ProcessState.STOPPED)
        else:
            self._set_state(ProcessState.CRASHED)
            self._report_error(f"exited with code {returncode}")
            if time.monotonic() - self._started_at >= STABLE_UPTIME:
                self._crash_count = 0
            if self.config.autorestart:
                self._schedule_restart()

    def _schedule_restart(self) -> None:
        """Schedule an autorestart after an exponential backoff (capped at `_MAX_RESTART_DELAY`)."""
        self._crash_count += 1
        delay = min(_BASE_RESTART_DELAY * (2 ** (self._crash_count - 1)), _MAX_RESTART_DELAY)
        self.log(f"--- autorestart in {delay:.0f}s (crash #{self._crash_count}) ---")
        self._restart_task = asyncio.create_task(self._delayed_restart(delay))

    async def _delayed_restart(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._restart_task = None
        await self.start()

    async def stop(self, timeout: float = _STOP_TIMEOUT) -> None:
        """Terminate the process, escalating to a hard kill after `timeout` seconds.

        Also cancels a pending autorestart, even if nothing is currently running, so
        stopping a crashed process prevents it from coming back on its own.
        """
        if self._restart_task is not None:
            self._restart_task.cancel()
            self._restart_task = None

        if self._proc is None or self.state not in (
            ProcessState.STARTING,
            ProcessState.RUNNING,
            ProcessState.STOPPING,
        ):
            return

        self._set_state(ProcessState.STOPPING)
        self._terminate()

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._kill()
            await self._proc.wait()

        if self._pump_task is not None:
            await self._pump_task
        if self._wait_task is not None:
            await self._wait_task

    def _terminate(self) -> None:
        """Ask the process to exit gracefully: SIGTERM on POSIX, CTRL_BREAK_EVENT on Windows.

        CTRL_BREAK_EVENT relies on the child having been spawned into its own process
        group (see `start()`), on a console being attached to send it through, and on
        the child handling the signal (Python, Node, and most well-behaved console
        apps do). Without a console, or if sending it fails outright, fall back to an
        immediate hard kill instead of waiting out the full stop timeout for nothing.
        """
        assert self._proc is not None
        if _IS_WINDOWS:
            if not _has_console():
                self._windows_force_kill()
                return
            try:
                self._proc.send_signal(signal.CTRL_BREAK_EVENT)
            except (ProcessLookupError, OSError, ValueError):
                self._windows_force_kill()
        else:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _kill(self) -> None:
        """Forcibly kill the process (and its group on POSIX; its process tree on Windows)."""
        assert self._proc is not None
        if _IS_WINDOWS:
            self._windows_force_kill()
        else:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _windows_force_kill(self) -> None:
        """Hard-kill the process tree on Windows via taskkill, with no graceful step."""
        assert self._proc is not None
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(self._proc.pid)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )

    async def restart(self) -> None:
        """Stop the process (if running) and start it again."""
        await self.stop()
        await self.start()
