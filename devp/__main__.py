"""CLI entry point: find devp.toml and launch the TUI, or scaffold a new config."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from devp import __version__
from devp.config import CONFIG_VERSION, ConfigError, check_versions, load_config

CONFIG_NAME = "devp.toml"

_console = Console(stderr=True)

VERSION_HEADER = f"""\
[devp]
version = "{__version__}"        # devp version that generated this file
config-version = "{CONFIG_VERSION}"   # config layout version; devp warns when it doesn't match
"""

STARTER_CONFIG = f"""\
# devp.toml: the processes devp runs. See the README for every option.

{VERSION_HEADER}
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
        usage="devp [-c PATH]\n       devp init [--force]\n       devp [-c PATH] upgrade-config",
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
    commands.add_parser(
        "upgrade-config",
        help=f"record this devp's version and config layout ({CONFIG_VERSION}) in the config",
    )
    args = parser.parse_args(argv)

    if args.command == "init":
        _init(force=args.force)
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

    # Deferred: loading Textual is the bulk of startup, and `init`/errors don't need it.
    from devp.app import DevpApp
    from devp.manager import ProcessManager

    DevpApp(ProcessManager(config), config_path=config_path).run()


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
