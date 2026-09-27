"""Parsing and validation for devp.toml configuration files."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised when devp.toml is missing or fails validation."""


@dataclass(frozen=True)
class ProcessConfig:
    """One validated `[[process]]` entry from devp.toml."""

    name: str
    command: str | list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    autostart: bool = True


def _display_path(path: Path) -> str:
    """Render `path` relative to the current directory when possible, for shorter error text."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def load_config(path: Path) -> list[ProcessConfig]:
    """Load and validate devp.toml at `path`, raising ConfigError on any problem."""
    display = _display_path(path)

    if not path.is_file():
        raise ConfigError(f"no config file found at {display}")

    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"could not parse {display}: {exc}") from exc

    raw_processes = data.get("process", [])
    if not isinstance(raw_processes, list) or not raw_processes:
        raise ConfigError(f"{display} must define at least one [[process]] entry")

    processes: list[ProcessConfig] = []
    seen_names: set[str] = set()

    for index, entry in enumerate(raw_processes):
        location = f"[[process]] entry #{index + 1}"

        if not isinstance(entry, dict):
            raise ConfigError(f"{location} must be a table")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{location}: 'name' is required and must be a non-empty string")
        if name in seen_names:
            raise ConfigError(f"{location}: duplicate process name '{name}'")
        seen_names.add(name)

        command = entry.get("command")
        if isinstance(command, list):
            if not command or not all(isinstance(part, str) for part in command):
                raise ConfigError(
                    f"{location} ('{name}'): 'command' list must contain only non-empty strings"
                )
        elif not isinstance(command, str) or not command.strip():
            raise ConfigError(
                f"{location} ('{name}'): 'command' is required and must be a string or list of strings"
            )

        cwd = entry.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise ConfigError(f"{location} ('{name}'): 'cwd' must be a string")

        env = entry.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise ConfigError(f"{location} ('{name}'): 'env' must be a table of string to string")

        autostart = entry.get("autostart", True)
        if not isinstance(autostart, bool):
            raise ConfigError(f"{location} ('{name}'): 'autostart' must be a boolean")

        processes.append(
            ProcessConfig(
                name=name,
                command=command,
                cwd=cwd,
                env=dict(env),
                autostart=autostart,
            )
        )

    return processes
