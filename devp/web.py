"""`devp --web`: run the processes headlessly and serve the TUI to a browser.

This devp (the host) owns the processes and the control API. Each browser session runs
`devp attach`, an ordinary TUI that views and drives the host through that API (see
`devp.remote`), so any number of tabs share one set of processes, and closing a tab
leaves them running. Textual's `textual-serve` does the terminal-in-a-browser part.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aiohttp import web
import textual_serve.server as textual_serve_server
from textual_serve.app_service import AppService
from textual_serve.server import Server

from devp.api import API_FILE_NAME, ControlServer
from devp.config import Config, ConfigError, file_stamp, load_config
from devp.hostlog import HostLog
from devp.manager import ProcessManager
from devp.process import ProcessState

_COOKIE = "devp_web_token"
_CONFIG_POLL_INTERVAL = 1.0


def attach_command(api_file: Path) -> str:
    """The shell command a browser session runs to show the TUI attached to this host."""
    argv = [sys.executable, "-m", "devp", "attach", "--api-file", str(api_file)]
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


class _TidyAppService(AppService):
    """textual-serve's per-session app process, with its pipes closed once it has stopped.

    textual-serve leaves the subprocess transport (stdin, stdout and stderr pipes) for
    the garbage collector, so on Windows exiting prints "Exception ignored ... I/O
    operation on closed pipe" for every browser session that ever connected.
    """

    async def stop(self) -> None:
        try:
            await super().stop()
        finally:
            transport = getattr(self._process, "_transport", None)
            if transport is not None:
                transport.close()


textual_serve_server.AppService = _TidyAppService  # the name Server.handle_websocket looks up


class WebHost(Server):
    """Owns the processes and API, and serves the attached TUI over HTTP behind a token."""

    def __init__(self, config: Config, config_path: Path, host: str, port: int) -> None:
        self.api_file = config_path.parent / API_FILE_NAME
        super().__init__(attach_command(self.api_file), host=host, port=port, title="devp")
        self.token = secrets.token_urlsafe(16)
        self.log_out = HostLog()
        self.manager = self._new_manager(config)
        self.api = ControlServer(lambda: self.manager, self._quit)
        self.config_path = config_path
        self._config_stamp = file_stamp(config_path)  # as loaded, so an early save isn't missed
        self._autostart: asyncio.Task[None] | None = None
        self._config_watcher: asyncio.Task[None] | None = None

    def _new_manager(self, config: Config) -> ProcessManager:
        """A manager whose process events are reported as log lines."""
        manager = ProcessManager(config)
        manager.build(
            on_state_change=self.log_out.process_state,
            on_error=self.log_out.process_error,
        )
        return manager

    def initialize_logging(self) -> None:
        # textual-serve would log every HTTP request; devp prints its own event lines.
        logging.basicConfig(level=logging.WARNING, format="%(message)s")

    @property
    def url(self) -> str:
        return f"{self.public_url}/?token={self.token}"

    async def _quit(self) -> None:
        # GracefulExit only stops the server when raised from a loop callback, not a task.
        asyncio.get_running_loop().call_soon(self.request_exit)

    async def _make_app(self) -> web.Application:
        app = await super()._make_app()
        app.middlewares.append(self._require_token)
        return app

    @web.middleware
    async def _require_token(self, request: web.Request, handler: Any) -> web.StreamResponse:
        """Let a request through only with the right token, in the URL or (after) a cookie."""
        in_url = request.query.get("token")
        given = in_url or request.cookies.get(_COOKIE)
        if not given or not hmac.compare_digest(given, self.token):
            self.log_out.blocked(request.remote or "unknown")
            raise web.HTTPForbidden(text="devp: missing or wrong token (open the URL devp printed)")
        response = await handler(request)
        if in_url and not isinstance(response, web.WebSocketResponse):
            response.set_cookie(_COOKIE, self.token, httponly=True, samesite="Strict")
        return response

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        peer = request.remote or "unknown"
        opened = time.monotonic()
        self.log_out.session(peer, joined=True)
        try:
            return await super().handle_websocket(request)
        finally:
            self.log_out.session(peer, joined=False, seconds=time.monotonic() - opened)

    async def _watch_config(self) -> None:
        """Reload when devp.toml is saved with a meaningful change (like the TUI does)."""
        while True:
            await asyncio.sleep(_CONFIG_POLL_INTERVAL)
            current = file_stamp(self.config_path)
            if current is None or current == self._config_stamp:
                continue
            self._config_stamp = current  # tried once per save
            await self.reload_config()

    async def reload_config(self) -> bool:
        """Load devp.toml and, if it changed, restart everything from it. Returns True if reloaded.

        An invalid file is reported and the running processes are left alone. Unlike the
        TUI there is nobody to ask, so a changed config is applied right away.
        """
        name = self.config_path.name
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            self.log_out.config_error(name, str(exc))
            return False
        if config == self.manager.config:
            return False  # e.g. only comments or whitespace changed
        active = (ProcessState.STARTING, ProcessState.RUNNING)
        self.log_out.reload_started(
            name, sum(r.state in active for r in self.manager.processes.values())
        )
        if self._autostart is not None:
            self._autostart.cancel()
        old = self.manager
        # Swapped in first, so API requests during the (slow) shutdown see the new set.
        self.manager = self._new_manager(config)
        await old.shutdown_all()
        self._autostart = asyncio.create_task(self.manager.autostart())
        for warning in config.warnings:
            self.log_out.warning(warning)
        self.log_out.reload_finished(len(self.manager.processes))
        return True

    async def on_startup(self, app: web.Application) -> None:
        await self.api.start(self.api_file)
        self._autostart = asyncio.create_task(self.manager.autostart())
        self._config_watcher = asyncio.create_task(self._watch_config())
        self.log_out.banner(
            self.url, self.config_path.name, len(self.manager.processes), self.api.port
        )

    async def on_shutdown(self, app: web.Application) -> None:
        self.log_out.shutting_down()
        try:
            await self.api.stop()
            if self._config_watcher is not None:
                self._config_watcher.cancel()
            if self._autostart is not None:
                self._autostart.cancel()
            await self.manager.shutdown_all()
        finally:
            await super().on_shutdown(app)


def serve(config: Config, config_path: Path, host: str, port: int) -> None:
    """Run the host until Ctrl+C (or `devp ctl quit`), then stop every process."""
    WebHost(config, config_path, host, port).serve()
