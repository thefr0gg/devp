"""CLI entry point: load ./devp.toml and launch the TUI."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from devp.app import DevpApp
from devp.config import ConfigError, load_config
from devp.process import ProcessManager

_console = Console(stderr=True)


def main() -> None:
    """Load devp.toml from the current directory and run the app, or exit with a clear error."""
    config_path = Path.cwd() / "devp.toml"

    try:
        configs = load_config(config_path)
    except ConfigError as exc:
        _print_config_error(exc)
        raise SystemExit(1) from exc

    manager = ProcessManager(configs)
    app = DevpApp(manager)
    app.run()


def _print_config_error(exc: ConfigError) -> None:
    """Render a configuration error as a styled panel on stderr instead of a raw traceback."""
    _console.print(
        Panel(
            escape(str(exc)),
            title="devp: configuration error",
            title_align="left",
            border_style="red",
            expand=False,
        )
    )


if __name__ == "__main__":
    main()
