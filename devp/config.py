"""Parsing and validation for devp.toml configuration files."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devp import __version__

CONFIG_VERSION = "1.1"
"""The layout version of devp.toml, recorded in its `[devp]` table as `config-version`.

Bump the minor part for a backward-compatible layout change (a new optional key, say)
and the major part for a breaking one (a renamed or removed key, a changed meaning).
devp warns when a config's version differs in the minor part, and refuses a config
whose major version differs, like Poetry does with its lock files.

History:
- 1.0: the initial layout.
- 1.1: `shell` on processes and cron jobs, and a `[defaults]` table.
"""


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
    ready_when: str | None = None  # regex matched against each output line
    ready_port: int | None = None  # TCP port on localhost that must accept connections
    ready_timeout: float = 60.0
    watch: list[str] = field(default_factory=list)  # globs; restart the process on changes
    shell: str | list[str] | None = None  # for string commands; None = the system default

    @property
    def has_ready_check(self) -> bool:
        return self.ready_when is not None or self.ready_port is not None


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
    shell: str | list[str] | None = None


EntryConfig = ProcessConfig | CronConfig


@dataclass(frozen=True)
class Config:
    """Every process and cron job declared in devp.toml."""

    processes: list[ProcessConfig]
    crons: list[CronConfig]
    # From the `[devp]` table: metadata about the file itself, so it doesn't count
    # when comparing configs (re-stamping a file isn't a change worth reloading for).
    devp_version: str | None = field(default=None, compare=False)
    config_version: str | None = field(default=None, compare=False)
    warnings: list[str] = field(default_factory=list, compare=False)


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


def _validate_ready(entry: dict[str, Any], location: str, name: str) -> dict[str, Any]:
    """Validate the optional readiness check: `ready_when` or `ready_port`, plus a timeout."""
    where = f"{location} ('{name}')"
    ready_when = entry.get("ready_when")
    ready_port = entry.get("ready_port")
    ready_timeout = entry.get("ready_timeout", 60)

    if ready_when is not None and ready_port is not None:
        raise ConfigError(f"{where}: set either 'ready_when' or 'ready_port', not both")
    if ready_when is not None:
        if not isinstance(ready_when, str) or not ready_when:
            raise ConfigError(f"{where}: 'ready_when' must be a non-empty string")
        try:
            re.compile(ready_when)
        except re.error as exc:
            raise ConfigError(
                f"{where}: 'ready_when' is not a valid regular expression: {exc}"
            ) from exc
    if ready_port is not None and not (_is_int(ready_port) and 1 <= ready_port <= 65535):
        raise ConfigError(f"{where}: 'ready_port' must be a port number (1-65535)")
    if not (_is_int(ready_timeout) or isinstance(ready_timeout, float)) or ready_timeout <= 0:
        raise ConfigError(f"{where}: 'ready_timeout' must be a positive number of seconds")

    return {
        "ready_when": ready_when,
        "ready_port": ready_port,
        "ready_timeout": float(ready_timeout),
    }


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_watch(entry: dict[str, Any], location: str, name: str) -> list[str]:
    watch = entry.get("watch", [])
    if not isinstance(watch, list) or not all(isinstance(p, str) and p.strip() for p in watch):
        raise ConfigError(f"{location} ('{name}'): 'watch' must be a list of glob patterns")
    return list(watch)


def _validate_shell(value: Any, where: str) -> str | list[str] | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, list) and value and all(isinstance(p, str) and p for p in value):
        return list(value)
    raise ConfigError(
        f"{where}: 'shell' must be a shell name or path (e.g. \"bash\"), "
        "or a non-empty list like [\"pwsh\", \"-Command\"]"
    )


def _entry_shell(
    entry: dict[str, Any], location: str, name: str, command: str | list[str], default: Any
) -> str | list[str] | None:
    """The entry's own `shell`, else `[defaults] shell`; only string commands use one."""
    where = f"{location} ('{name}')"
    if "shell" in entry:
        if isinstance(command, list):
            raise ConfigError(
                f"{where}: 'shell' only applies to a string 'command'; a list 'command' "
                "runs directly, without a shell"
            )
        return _validate_shell(entry["shell"], where)
    return default


def _parse_process(
    entry: dict[str, Any], location: str, seen_names: set[str], default_shell: Any = None
) -> ProcessConfig:
    name = _validate_name(entry, location, seen_names)
    command = _validate_command(entry, location, name)
    return ProcessConfig(
        name=name,
        command=command,
        cwd=_validate_cwd(entry, location, name),
        env=_validate_env(entry, location, name),
        autostart=_validate_bool(entry, "autostart", location, name, default=True),
        autorestart=_validate_bool(entry, "autorestart", location, name, default=False),
        depends_on=_validate_depends_on(entry, location, name),
        **_validate_ready(entry, location, name),
        watch=_validate_watch(entry, location, name),
        shell=_entry_shell(entry, location, name, command, default_shell),
    )


def _parse_cron(
    entry: dict[str, Any], location: str, seen_names: set[str], default_shell: Any = None
) -> CronConfig:
    name = _validate_name(entry, location, seen_names)
    command = _validate_command(entry, location, name)

    schedule = entry.get("schedule")
    if not isinstance(schedule, str) or not schedule.strip():
        raise ConfigError(f"{location} ('{name}'): 'schedule' is required and must be a string")
    from croniter import croniter  # deferred: only needed when crons are configured

    if not croniter.is_valid(schedule):
        raise ConfigError(f"{location} ('{name}'): '{schedule}' is not a valid cron schedule")

    return CronConfig(
        name=name,
        command=command,
        schedule=schedule,
        cwd=_validate_cwd(entry, location, name),
        env=_validate_env(entry, location, name),
        enabled=_validate_bool(entry, "enabled", location, name, default=True),
        depends_on=_validate_depends_on(entry, location, name),
        shell=_entry_shell(entry, location, name, command, default_shell),
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


def _parse_version(value: str) -> tuple[int, ...] | None:
    """'1.2' -> (1, 2); None when it isn't dot-separated integers."""
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return None


def _read_metadata(data: dict[str, Any], display: str) -> tuple[str | None, str | None]:
    """The `version` and `config-version` recorded in the `[devp]` table, if any."""
    meta = data.get("devp", {})
    if not isinstance(meta, dict):
        raise ConfigError(f"{display}: '[devp]' must be a table")
    devp_version = meta.get("version")
    config_version = meta.get("config-version")
    for key, value in (("version", devp_version), ("config-version", config_version)):
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{display}: [devp] '{key}' must be a string")
    if config_version is not None:
        parsed = _parse_version(config_version)
        if parsed is None or len(parsed) != 2:
            raise ConfigError(
                f"{display}: [devp] config-version must look like \"{CONFIG_VERSION}\", "
                f"not \"{config_version}\""
            )
    return devp_version, config_version


def check_versions(display: str, devp_version: str | None, config_version: str | None) -> list[str]:
    """Compare a config's recorded versions with this devp's.

    Returns warnings for differences devp can live with; raises ConfigError for a
    different major config-version, whose layout this devp can't read reliably.
    """
    upgrade = "Run `devp upgrade-config` once you've checked it, to record the current version."
    if config_version is None:
        return [
            f"{display} doesn't record a config-version, so devp can't tell which layout "
            f"it was written for (this devp uses {CONFIG_VERSION}). {upgrade}"
        ]

    warnings = []
    current = _parse_version(CONFIG_VERSION)
    found = _parse_version(config_version)
    assert current is not None and found is not None
    if found[0] != current[0]:
        if found[0] < current[0]:
            advice = (
                f"Update it to the {current[0]}.x layout (see the README), "
                "then run `devp upgrade-config`."
            )
        else:
            advice = "Update devp to use it."
        raise ConfigError(
            f"{display} uses config layout {config_version}, which this devp "
            f"({__version__}, layout {CONFIG_VERSION}) can't read. {advice}"
        )
    if found < current:
        warnings.append(
            f"{display} uses config layout {config_version}; this devp uses "
            f"{CONFIG_VERSION}. It still works as is. {upgrade}"
        )
    elif found > current:
        warnings.append(
            f"{display} uses config layout {config_version}, newer than this devp "
            f"understands ({CONFIG_VERSION}); settings added since may be ignored. "
            "Update devp."
        )

    generated_by = _parse_version(devp_version) if devp_version else None
    running = _parse_version(__version__)
    if generated_by and running and generated_by > running:
        warnings.append(
            f"{display} was generated by devp {devp_version}, newer than this one "
            f"({__version__}). Update devp."
        )
    return warnings


def load_config(path: Path) -> Config:
    """Load and validate devp.toml at `path`, raising ConfigError on any problem."""
    display = _display_path(path)

    if not path.is_file():
        raise ConfigError(f"no config file found at {display}")

    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"could not parse {display}: {exc}") from exc

    devp_version, config_version = _read_metadata(data, display)
    warnings = check_versions(path.name, devp_version, config_version)

    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError(f"{display}: '[defaults]' must be a table")
    default_shell = _validate_shell(defaults.get("shell"), f"{display} [defaults]")

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
        processes.append(_parse_process(entry, location, seen_names, default_shell))

    crons: list[CronConfig] = []
    for index, entry in enumerate(raw_crons):
        location = f"[[cron]] entry #{index + 1}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{location} must be a table")
        crons.append(_parse_cron(entry, location, seen_names, default_shell))

    all_entries: dict[str, EntryConfig] = {c.name: c for c in (*processes, *crons)}
    _validate_dependency_graph(all_entries)

    return Config(
        processes=processes,
        crons=crons,
        devp_version=devp_version,
        config_version=config_version,
        warnings=warnings,
    )
