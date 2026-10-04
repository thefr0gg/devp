"""A local control API, so other programs (and AI agents) can drive a running devp.

devp listens on a TCP port bound to 127.0.0.1 and speaks newline-delimited JSON: one
request object per line, one response object per line. Every request carries the
token from the discovery file (`.devp-api.json`, next to devp.toml), which is how a
client finds the port and proves it may control this devp::

    -> {"id": 1, "token": "...", "method": "start", "params": {"name": "web"}}
    <- {"id": 1, "ok": true, "result": {"name": "web", "state": "running", ...}}
    <- {"id": 2, "ok": false, "error": "no process named 'nope'"}
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from devp.ansi import strip_ansi
from devp.cron import CronJob
from devp.manager import ProcessManager, Runnable

API_FILE_NAME = ".devp-api.json"
_MAX_REQUEST_BYTES = 1024 * 1024
_DEFAULT_LOG_LINES = 100


class ApiError(Exception):
    """A request that can't be carried out; its message goes back to the client."""


def _describe(runnable: Runnable) -> dict[str, Any]:
    is_cron = isinstance(runnable, CronJob)
    info: dict[str, Any] = {
        "name": runnable.config.name,
        "kind": "cron" if is_cron else "process",
        "state": runnable.state.name.lower(),
        "pid": runnable.pid,
        "exit_code": runnable.exit_code,
        "uptime": runnable.uptime,
        "restarts": max(runnable.start_count - 1, 0),
        "last_error": runnable.last_error,
    }
    if isinstance(runnable, CronJob):
        info["schedule"] = runnable.config.schedule
        next_run = runnable.next_run_at
        info["next_run_at"] = next_run.isoformat() if next_run is not None else None
    return info


def _read_logs(runnable: Runnable, since: Any, count: int) -> tuple[list[str], int]:
    """The last `count` buffered lines, or just those after cursor `since`, plus the next cursor.

    A cursor is a running total of lines ever written, so a client polling with the
    `next` it was last given receives every line exactly once (minus any the
    buffer has already dropped).
    """
    if since is not None and (isinstance(since, bool) or not isinstance(since, int) or since < 0):
        raise ApiError("'since' must be a non-negative integer")
    total = runnable.lines_total
    buffered = list(runnable.output)
    if since is not None:
        count = min(count, total - since)
    lines = buffered[-count:] if count > 0 else []
    return lines, total


class ControlServer:
    """Serves the control API for whichever `ProcessManager` `get_manager` returns.

    The manager is looked up per request because the app replaces it on a config reload.
    """

    def __init__(
        self,
        get_manager: Callable[[], ProcessManager],
        on_quit: Callable[[], Awaitable[None]],
        on_stop_all: Callable[[], None] | None = None,
    ) -> None:
        self._get_manager = get_manager
        self._on_quit = on_quit
        self._on_stop_all = on_stop_all
        self._server: asyncio.Server | None = None
        self._token = secrets.token_hex(16)
        self._file: Path | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._background: set[asyncio.Task[None]] = set()  # kept alive until done
        # Bumped whenever the manager is swapped (a config reload), so a client holding
        # entries from the old one can tell they are stale. Holding the manager keeps
        # the comparison honest: a freed manager's identity can't be reused.
        self._epoch_manager: ProcessManager | None = None
        self._epoch = 0
        self.port: int | None = None

    async def start(self, file: Path) -> None:
        """Begin listening on a free local port and publish it (with the token) to `file`."""
        self._server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", 0, limit=_MAX_REQUEST_BYTES
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self._file = file
        info = {"host": "127.0.0.1", "port": self.port, "token": self._token, "pid": os.getpid()}
        # Created owner-only before the token goes in, so it is never briefly readable.
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(info, handle)

    async def stop(self) -> None:
        """Stop listening and remove the discovery file (if it's still ours)."""
        if self._server is not None:
            self._server.close()
            self._server = None
        for task in list(self._tasks):
            task.cancel()
        if self._file is not None:
            with contextlib.suppress(OSError, ValueError):
                if json.loads(self._file.read_text()).get("token") == self._token:
                    self._file.unlink()
            self._file = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            while line := await reader.readline():
                response = await self._respond(line)
                writer.write(json.dumps(response).encode() + b"\n")
                await writer.drain()
                if response.get("result") == {"quitting": True}:
                    quit_task = asyncio.create_task(self._on_quit())
                    self._background.add(quit_task)
                    quit_task.add_done_callback(self._background.discard)
        except (ConnectionError, asyncio.CancelledError, ValueError):
            pass  # a client that vanished or sent an oversized line
        finally:
            if task is not None:
                self._tasks.discard(task)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _respond(self, line: bytes) -> dict[str, Any]:
        request_id = None
        try:
            try:
                request = json.loads(line)
            except ValueError as exc:
                raise ApiError("request is not valid JSON") from exc
            if not isinstance(request, dict):
                raise ApiError("request must be a JSON object")
            request_id = request.get("id")
            token = request.get("token")
            if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
                raise ApiError("missing or wrong token")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ApiError("'method' must be a string and 'params' an object")
            result = await self._dispatch(method, params)
        except ApiError as exc:
            return {"id": request_id, "ok": False, "error": str(exc)}
        except Exception as exc:  # never let one bad request take the server down
            return {"id": request_id, "ok": False, "error": f"internal error: {exc}"}
        return {"id": request_id, "ok": True, "result": result}

    def _lookup(self, params: dict[str, Any]) -> Runnable:
        name = params.get("name")
        if not isinstance(name, str):
            raise ApiError("'name' is required")
        runnable = self._get_manager().processes.get(name)
        if runnable is None:
            raise ApiError(f"no process named '{name}'")
        return runnable

    def _sync(self, manager: ProcessManager, params: dict[str, Any]) -> dict[str, Any]:
        """Every entry's state plus the raw log lines each is past its cursor, in one round trip."""
        cursors = params.get("cursors", {})
        if not isinstance(cursors, dict):
            raise ApiError("'cursors' must be an object of name to line cursor")
        logs = {}
        for name, runnable in manager.processes.items():
            lines, next_cursor = _read_logs(runnable, cursors.get(name), runnable.output.maxlen or 0)
            logs[name] = {"lines": lines, "next": next_cursor}  # raw: a UI renders the colors
        if manager is not self._epoch_manager:
            self._epoch_manager = manager
            self._epoch += 1
        return {
            "epoch": self._epoch,
            "entries": [_describe(r) for r in manager.processes.values()],
            "logs": logs,
        }

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        manager = self._get_manager()
        if method == "ping":
            return {"pong": True, "pid": os.getpid()}
        if method == "list":
            return [_describe(r) for r in manager.processes.values()]
        if method == "status":
            return _describe(self._lookup(params))
        if method in ("start", "stop", "restart"):
            runnable = self._lookup(params)
            await getattr(runnable, method)()
            if method != "stop" and params.get("wait_ready") and hasattr(runnable, "wait_ready"):
                await runnable.wait_ready()
            return _describe(runnable)
        if method == "start_all":
            await manager.start_all()
            return [_describe(r) for r in manager.processes.values()]
        if method == "stop_all":
            if self._on_stop_all is not None:
                self._on_stop_all()
            await manager.stop_all()
            return [_describe(r) for r in manager.processes.values()]
        if method == "logs":
            runnable = self._lookup(params)
            count = params.get("lines", _DEFAULT_LOG_LINES)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ApiError("'lines' must be a non-negative integer")
            lines, next_cursor = _read_logs(runnable, params.get("since"), count)
            if not params.get("raw"):
                lines = [strip_ansi(line) for line in lines]
            return {"name": runnable.config.name, "lines": lines, "next": next_cursor}
        if method == "sync":
            return self._sync(manager, params)
        if method == "quit":
            return {"quitting": True}
        raise ApiError(f"unknown method '{method}'")


def find_api_file(start: Path) -> Path | None:
    """The nearest discovery file in `start` or one of its parents, if any."""
    for directory in (start, *start.parents):
        candidate = directory / API_FILE_NAME
        if candidate.is_file():
            return candidate
    return None


def call(file: Path, method: str, params: dict[str, Any] | None = None, timeout: float = 120) -> Any:
    """Send one request to the devp described by `file` and return its result.

    Raises ApiError if devp can't be reached or rejects the request.
    """
    try:
        info = json.loads(file.read_text())
        request = {"id": 1, "token": info["token"], "method": method, "params": params or {}}
        with socket.create_connection((info["host"], info["port"]), timeout=timeout) as sock:
            sock.sendall(json.dumps(request).encode() + b"\n")
            reply = sock.makefile("rb").readline()
    except (OSError, ValueError, KeyError) as exc:
        raise ApiError(f"can't reach devp via {file}: {exc}") from exc
    if not reply:
        raise ApiError("devp closed the connection without answering")
    response = json.loads(reply)
    if not response.get("ok"):
        raise ApiError(response.get("error", "request failed"))
    return response["result"]

