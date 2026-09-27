"""CLI entry point: find devp.toml and launch the TUI, or scaffold a new config."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from devp import __version__
from devp.config import ConfigError, load_config

CONFIG_NAME = "devp.toml"

_console = Console(stderr=True)

STARTER_CONFIG = """\
# devp.toml: the processes devp runs. See the README for every option.

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
        usage="devp [-c PATH]\n       devp init [--force]",
        description="A simple, TOML-configured process multiplexer TUI.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        metavar="PATH",
        help=f"config file to use (default: the nearest {CONFIG_NAME} here or in a parent)",
    )
    parser.add_argument("--version", action="version", version=f"devp {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    init = commands.add_parser("init", help=f"create a starter {CONFIG_NAME} in this directory")
    init.add_argument("--force", action="store_true", help=f"overwrite an existing {CONFIG_NAME}")
    args = parser.parse_args(argv)

    if args.command == "init":
        _init(force=args.force)
        return

    config_path = args.config or find_config(Path.cwd())
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
    os.chdir(config_path.resolve().parent)

    # Deferred: loading Textual is the bulk of startup, and `init`/errors don't need it.
    from devp.app import DevpApp
    from devp.manager import ProcessManager

    DevpApp(ProcessManager(config)).run()


def _init(*, force: bool) -> None:
    path = Path.cwd() / CONFIG_NAME
    if path.exists() and not force:
        _print_error(f"{CONFIG_NAME} already exists here (use `devp init --force` to overwrite it)")
        raise SystemExit(1)
    path.write_text(STARTER_CONFIG)
    Console().print(f"Created {CONFIG_NAME}; edit it, then run [bold]devp[/bold].")


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
