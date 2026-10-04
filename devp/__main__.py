"""CLI entry point: find devp.toml and launch the TUI, or scaffold a new config."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from devp import __version__
from devp.api import ApiError, call, find_api_file
from devp.templates import JS_MANAGERS, TEMPLATES, detect, detect_js_manager, render
from devp.config import CONFIG_VERSION, ConfigError, check_versions, load_config

CONFIG_NAME = "devp.toml"
CTL_ACTIONS = (
    "ping", "list", "status", "start", "stop", "restart", "start-all", "stop-all", "logs", "quit"
)

_console = Console(stderr=True)

VERSION_HEADER = f"""\
[devp]
version = "{__version__}"        # devp version that generated this file
config-version = "{CONFIG_VERSION}"   # config layout version; devp warns when it doesn't match
"""

STARTER_CONFIG = f"""\
# devp.toml: the processes devp runs. See the README for every option.

{VERSION_HEADER}
# [defaults]
# shell = "bash"             # shell for string commands (default: sh, or cmd on Windows)

[[process]]
name = "web"
command = "python -m http.server 8000"
ready_port = 8000            # dependents wait until this port accepts connections

# [[process]]
# name = "worker"
# command = ["python", "worker.py"]
# depends_on = ["web"]       # start after 'web' is ready
# autorestart = true         # bring it back if it crashes
# watch = ["**/*.py"]        # restart when these files change

# [[cron]]
# name = "cleanup"
# command = "python cleanup.py"
# schedule = "*/15 * * * *"  # every 15 minutes
"""


def find_config(start: Path) -> Path | None:
    """The nearest devp.toml in `start` or one of its parent directories, if any."""
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> None:
    """Parse the command line, then run the TUI (or `devp init`)."""
    parser = argparse.ArgumentParser(
        prog="devp",
        usage=(
            "devp [-c PATH] [--no-api | --web [--host HOST] [--port PORT]]\n"
            "       devp init [-t TEMPLATE] [-p PM] [--force | --list]\n"
            "       devp [-c PATH] upgrade-config\n       devp [-c PATH] ctl ACTION [NAME]"
        ),
        description="A simple, TOML-configured process multiplexer TUI.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        metavar="PATH",
        help=f"config file to use (default: the nearest {CONFIG_NAME} here or in a parent)",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="don't start the local control API (see `devp ctl`)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="serve the TUI to a browser instead of the terminal (turns the API on)",
    )
    parser.add_argument("--host", default="localhost", help="with --web: address to bind (localhost)")
    parser.add_argument("--port", type=int, default=8000, help="with --web: port to serve on (8000)")
    parser.add_argument("--version", action="version", version=f"devp {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    init = commands.add_parser("init", help=f"create a starter {CONFIG_NAME} in this directory")
    init.add_argument("--force", action="store_true", help=f"overwrite an existing {CONFIG_NAME}")
    init.add_argument(
        "-t",
        "--template",
        choices=sorted(TEMPLATES),
        metavar="NAME",
        help="project template, skipping the menu (see --list)",
    )
    init.add_argument(
        "-p",
        "--package-manager",
        choices=JS_MANAGERS,
        metavar="PM",
        help="npm, yarn, pnpm or bun, for JavaScript templates (default: detected)",
    )
    init.add_argument("--list", action="store_true", help="list the templates and exit")
    commands.add_parser(
        "upgrade-config",
        help=f"record this devp's version and config layout ({CONFIG_VERSION}) in the config",
    )
    ctl = commands.add_parser(
        "ctl",
        help="control a running devp: list, status, start, stop, restart, logs, quit, ...",
    )
    ctl.add_argument("action", choices=CTL_ACTIONS, metavar="ACTION", help=", ".join(CTL_ACTIONS))
    ctl.add_argument("name", nargs="?", help="the process or cron job (for status/start/...)")
    ctl.add_argument("-n", "--lines", type=int, help="with logs: how many recent lines")
    ctl.add_argument("--wait-ready", action="store_true", help="with start/restart: wait until ready")
    attach = commands.add_parser("attach", help=argparse.SUPPRESS)  # what --web runs per session
    attach.add_argument("--api-file", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.web and args.no_api:
        parser.error("--web needs the API; it can't be combined with --no-api")

    if args.command == "init":
        _init(
            force=args.force,
            template=args.template,
            package_manager=args.package_manager,
            list_templates=args.list,
        )
        return

    if args.command == "attach":
        _attach(args.api_file)
        return

    if args.command == "ctl":
        _ctl(args)
        return

    config_path = args.config or find_config(Path.cwd())
    if args.command == "upgrade-config":
        _upgrade_config(config_path)
        return
    if config_path is None:
        _print_error(
            f"no {CONFIG_NAME} found in {Path.cwd()} or any parent directory.\n"
            f"Run `devp init` to create one here, or pass `-c path/to/{CONFIG_NAME}`."
        )
        raise SystemExit(1)

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        _print_error(str(exc))
        raise SystemExit(1) from exc

    # Run from the config's directory, so relative `cwd` and `watch` paths always mean
    # the same thing no matter where devp was launched from.
    config_path = config_path.resolve()
    os.chdir(config_path.parent)

    if args.web:
        from devp.web import serve

        serve(config, config_path, args.host, args.port)
        return

    # Deferred: loading Textual is the bulk of startup, and `init`/errors don't need it.
    from devp.app import DevpApp
    from devp.manager import ProcessManager

    DevpApp(ProcessManager(config), config_path=config_path, enable_api=not args.no_api).run()


def stamp_versions(text: str) -> str:
    """Set the `[devp]` table's version and config-version, leaving the rest untouched.

    Edits the text rather than re-serializing the TOML, so comments, ordering, and
    formatting survive. A file without a `[devp]` table gets one just before its first
    table: above any leading comments' content, but never above top-level keys, which
    would otherwise end up inside `[devp]`.
    """
    values = {"version": __version__, "config-version": CONFIG_VERSION}
    lines = text.splitlines(keepends=True)
    header = next(
        (i for i, line in enumerate(lines) if re.match(r"\s*\[devp\]\s*(#.*)?$", line)), None
    )
    if header is None:
        first_table = next(
            (i for i, line in enumerate(lines) if re.match(r"\s*\[", line)), len(lines)
        )
        if lines and not lines[-1].endswith("\n") and first_table == len(lines):
            lines[-1] += "\n"
        lines.insert(first_table, VERSION_HEADER + "\n")
        return "".join(lines)

    end = next(
        (i for i in range(header + 1, len(lines)) if re.match(r"\s*\[", lines[i])), len(lines)
    )
    missing = dict(values)
    for i in range(header + 1, end):
        for key, value in values.items():
            match = re.match(
                rf'(\s*"?{re.escape(key)}"?\s*=\s*)("[^"]*"|\'[^\']*\'|[^#\s]*)(.*)$',
                lines[i].rstrip("\r\n"),
            )
            if match:
                ending = lines[i][len(lines[i].rstrip("\r\n")) :]
                lines[i] = f'{match.group(1)}"{value}"{match.group(3)}{ending}'
                missing.pop(key, None)
    for key, value in reversed(list(missing.items())):
        lines.insert(header + 1, f'{key} = "{value}"\n')
    return "".join(lines)


def _upgrade_config(config_path: Path | None) -> None:
    if config_path is None or not config_path.is_file():
        _print_error(f"no {CONFIG_NAME} found here or in a parent directory")
        raise SystemExit(1)
    try:
        config = load_config(config_path)
    except ConfigError as exc:  # includes a different major layout: needs a manual update
        _print_error(str(exc))
        raise SystemExit(1) from exc
    config_path.write_text(stamp_versions(config_path.read_text()))
    before = config.config_version or "none"
    Console().print(
        f"Updated {config_path.name}: devp {__version__}, config layout {CONFIG_VERSION} "
        f"(was {before})."
    )


def _attach(api_file: Path) -> None:
    """Show the TUI for a devp already running (the host `--web` starts)."""
    from devp.app import DevpApp
    from devp.remote import RemoteManager

    try:
        manager = RemoteManager(api_file)
    except ApiError as exc:
        print(f"devp attach: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    app = DevpApp(manager, enable_api=False)

    def follow(current: RemoteManager) -> None:
        """Close the view if the host goes away; rebuild it if the host reloads its config."""
        current.on_disconnect = app.exit

        async def swap() -> None:
            try:
                fresh = await asyncio.to_thread(RemoteManager, api_file)
            except ApiError:
                app.exit()
                return
            follow(fresh)
            await app.adopt_manager(fresh)
            app.notify("Config reloaded", severity="information", timeout=3)

        current.on_reconfigured = lambda: app.run_worker(swap(), group="reattach", exclusive=True)

    follow(manager)
    app.run()


def _ctl(args: argparse.Namespace) -> None:
    """Send one control request to the running devp and print its JSON result."""
    method = args.action.replace("-", "_")
    needs_name = method in ("status", "start", "stop", "restart", "logs")
    if needs_name and not args.name:
        _print_error(f"`devp ctl {args.action}` needs a process name")
        raise SystemExit(2)
    params: dict[str, object] = {"name": args.name} if needs_name else {}
    if args.lines is not None:
        params["lines"] = args.lines
    if args.wait_ready:
        params["wait_ready"] = True

    start = args.config.resolve().parent if args.config else Path.cwd()
    api_file = find_api_file(start)
    if api_file is None:
        print(f"devp ctl: no running devp found from {start} (is devp running here?)", file=sys.stderr)
        raise SystemExit(1)
    try:
        result = call(api_file, method, params)
    except ApiError as exc:
        print(f"devp ctl: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _init(
    *,
    force: bool,
    template: str | None = None,
    package_manager: str | None = None,
    list_templates: bool = False,
) -> None:
    console = Console()
    if list_templates:
        for name, entry in TEMPLATES.items():
            console.print(f"  [bold]{name:<10}[/bold] {entry.description}")
        return

    path = Path.cwd() / CONFIG_NAME
    if path.exists() and not force:
        _print_error(f"{CONFIG_NAME} already exists here (use `devp init --force` to overwrite it)")
        raise SystemExit(1)

    how = "chosen"
    if template is None:
        suggested = detect(Path.cwd())
        if _is_interactive():
            from devp.picker import choose_template

            template = choose_template(
                {name: entry.description for name, entry in TEMPLATES.items()}, suggested
            )
            if template is None:
                console.print("Cancelled; nothing was written.")
                raise SystemExit(1)
        else:  # no terminal to ask on (a script, CI): fall back to the best guess
            template = suggested or "generic"
            how = "detected"
    if template == "generic":
        path.write_text(STARTER_CONFIG)
        console.print(f"Created {CONFIG_NAME}; edit it, then run [bold]devp[/bold].")
        return

    body = render(template, Path.cwd(), package_manager)
    path.write_text(
        "# devp.toml: the processes devp runs. See the README for every option.\n\n"
        f"{VERSION_HEADER}\n{body}"
    )
    uses_js = template in ("node", "react", "next", "vue")
    manager = f", using {package_manager or detect_js_manager(Path.cwd())}" if uses_js else ""
    console.print(
        f"Created {CONFIG_NAME} from the [bold]{template}[/bold] template ({how}{manager}); "
        "check the commands, then run [bold]devp[/bold]."
    )


def _print_error(message: str) -> None:
    """Render an error as a styled panel on stderr instead of a raw traceback."""
    _console.print(
        Panel(
            escape(message),
            title="devp: configuration error",
            title_align="left",
            border_style="red",
            expand=False,
        )
    )


if __name__ == "__main__":
    main()
