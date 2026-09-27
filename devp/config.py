"""Parsing and validation for devp.toml configuration files."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from croniter import croniter


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
    autorestart: bool = False
    depends_on: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CronConfig:
    """One validated `[[cron]]` entry from devp.toml."""

    name: str
    command: str | list[str]
    schedule: str
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    depends_on: list[str] = field(default_factory=list)


EntryConfig = ProcessConfig | CronConfig


@dataclass(frozen=True)
class Config:
    """Every process and cron job declared in devp.toml."""

    processes: list[ProcessConfig]
    crons: list[CronConfig]


def _display_path(path: Path) -> str:
    """Render `path` relative to the current directory when possible, for shorter error text."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _validate_name(entry: dict[str, Any], location: str, seen_names: set[str]) -> str:
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{location}: 'name' is required and must be a non-empty string")
    if name in seen_names:
        raise ConfigError(f"{location}: duplicate name '{name}'")
    seen_names.add(name)
    return name


def _validate_command(entry: dict[str, Any], location: str, name: str) -> str | list[str]:
    command = entry.get("command")
    if isinstance(command, list):
        if not command or not all(isinstance(part, str) for part in command):
            raise ConfigError(
                f"{location} ('{name}'): 'command' list must contain only non-empty strings"
            )
        return command
    if not isinstance(command, str) or not command.strip():
        raise ConfigError(
            f"{location} ('{name}'): 'command' is required and must be a string or list of strings"
        )
    return command


def _validate_cwd(entry: dict[str, Any], location: str, name: str) -> str | None:
    cwd = entry.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise ConfigError(f"{location} ('{name}'): 'cwd' must be a string")
    return cwd


def _validate_env(entry: dict[str, Any], location: str, name: str) -> dict[str, str]:
    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ConfigError(f"{location} ('{name}'): 'env' must be a table of string to string")
    return dict(env)


def _validate_bool(entry: dict[str, Any], key: str, location: str, name: str, default: bool) -> bool:
    value = entry.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{location} ('{name}'): '{key}' must be a boolean")
    return value


def _validate_depends_on(entry: dict[str, Any], location: str, name: str) -> list[str]:
    depends_on = entry.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
        raise ConfigError(f"{location} ('{name}'): 'depends_on' must be a list of strings")
    return list(depends_on)


def _parse_process(entry: dict[str, Any], location: str, seen_names: set[str]) -> ProcessConfig:
    name = _validate_name(entry, location, seen_names)
    return ProcessConfig(
        name=name,
        command=_validate_command(entry, location, name),
        cwd=_validate_cwd(entry, location, name),
        env=_validate_env(entry, location, name),
        autostart=_validate_bool(entry, "autostart", location, name, default=True),
        autorestart=_validate_bool(entry, "autorestart", location, name, default=False),
        depends_on=_validate_depends_on(entry, location, name),
    )


def _parse_cron(entry: dict[str, Any], location: str, seen_names: set[str]) -> CronConfig:
    name = _validate_name(entry, location, seen_names)

    schedule = entry.get("schedule")
    if not isinstance(schedule, str) or not schedule.strip():
        raise ConfigError(f"{location} ('{name}'): 'schedule' is required and must be a string")
    if not croniter.is_valid(schedule):
        raise ConfigError(f"{location} ('{name}'): '{schedule}' is not a valid cron schedule")

    return CronConfig(
        name=name,
        command=_validate_command(entry, location, name),
        schedule=schedule,
        cwd=_validate_cwd(entry, location, name),
        env=_validate_env(entry, location, name),
        enabled=_validate_bool(entry, "enabled", location, name, default=True),
        depends_on=_validate_depends_on(entry, location, name),
    )


def _entry_autostarts(entry: EntryConfig) -> bool:
    return entry.autostart if isinstance(entry, ProcessConfig) else entry.enabled


def _validate_dependency_graph(entries: dict[str, EntryConfig]) -> None:
    """Check every `depends_on` reference exists, is autostarted, and forms no cycle."""
    for entry in entries.values():
        for dep in entry.depends_on:
            if dep == entry.name:
                raise ConfigError(f"'{entry.name}' cannot declare depends_on on itself")
            if dep not in entries:
                raise ConfigError(f"'{entry.name}' depends_on unknown entry '{dep}'")
            if not _entry_autostarts(entries[dep]):
                flag = "autostart" if isinstance(entries[dep], ProcessConfig) else "enabled"
                raise ConfigError(
                    f"'{entry.name}' depends_on '{dep}', but '{dep}' has {flag} = false "
                    "and would never start"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str, path: list[str]) -> None:
        if name in visited:
            return
        if name in visiting:
            cycle = " -> ".join([*path, name])
            raise ConfigError(f"circular depends_on: {cycle}")
        visiting.add(name)
        for dep in entries[name].depends_on:
            visit(dep, [*path, name])
        visiting.remove(name)
        visited.add(name)

    for name in entries:
        visit(name, [])


def load_config(path: Path) -> Config:
    """Load and validate devp.toml at `path`, raising ConfigError on any problem."""
    display = _display_path(path)

    if not path.is_file():
        raise ConfigError(f"no config file found at {display}")

    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"could not parse {display}: {exc}") from exc

    raw_processes = data.get("process", [])
    if not isinstance(raw_processes, list):
        raise ConfigError(f"{display}: '[[process]]' must be an array of tables")

    raw_crons = data.get("cron", [])
    if not isinstance(raw_crons, list):
        raise ConfigError(f"{display}: '[[cron]]' must be an array of tables")

    if not raw_processes and not raw_crons:
        raise ConfigError(f"{display} must define at least one [[process]] or [[cron]] entry")

    seen_names: set[str] = set()

    processes: list[ProcessConfig] = []
    for index, entry in enumerate(raw_processes):
        location = f"[[process]] entry #{index + 1}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{location} must be a table")
        processes.append(_parse_process(entry, location, seen_names))

    crons: list[CronConfig] = []
    for index, entry in enumerate(raw_crons):
        location = f"[[cron]] entry #{index + 1}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{location} must be a table")
        crons.append(_parse_cron(entry, location, seen_names))

    all_entries: dict[str, EntryConfig] = {c.name: c for c in (*processes, *crons)}
    _validate_dependency_graph(all_entries)

    return Config(processes=processes, crons=crons)
