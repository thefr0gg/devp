"""A small polling file watcher, used to restart a process when its source files change."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

# Directories that are never worth scanning: VCS metadata, dependencies, and caches.
# Skipping them keeps a scan of a typical project to a few thousand files.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
_POLL_INTERVAL = 1.0

Snapshot = dict[str, tuple[int, int]]  # relative path -> (mtime_ns, size)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob into a regex over '/'-separated relative paths.

    `*` and `?` stay within one path segment; `**` spans any number of segments,
    so `src/**/*.py` matches both `src/app.py` and `src/pkg/mod.py`.
    """
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _literal_prefix(pattern: str) -> str:
    """The leading directories of `pattern` that contain no wildcards (where to start walking)."""
    parts = pattern.split("/")[:-1]
    prefix = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        prefix.append(part)
    return "/".join(prefix)


class FileWatcher:
    """Polls files matching `patterns` under `base_dir` and reports which ones changed."""

    def __init__(
        self,
        patterns: Iterable[str],
        base_dir: str | os.PathLike[str],
        on_change: Callable[[list[str]], Awaitable[None]],
        interval: float | None = None,
    ) -> None:
        normalized = [p.replace("\\", "/").removeprefix("./") for p in patterns]
        self._regexes = [_glob_to_regex(p) for p in normalized]
        self._roots = sorted({_literal_prefix(p) for p in normalized})
        self._base_dir = Path(base_dir)
        self._on_change = on_change
        self._interval = _POLL_INTERVAL if interval is None else interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        # Scans run in a thread so a large tree never stalls the event loop (and the UI).
        snapshot = await asyncio.to_thread(self.scan)
        while True:
            await asyncio.sleep(self._interval)
            current = await asyncio.to_thread(self.scan)
            changed = sorted(
                path
                for path in current.keys() | snapshot.keys()
                if current.get(path) != snapshot.get(path)
            )
            snapshot = current
            if changed:
                await self._on_change(changed)

    def scan(self) -> Snapshot:
        """Stat every watched file: added, removed, and modified files all show up as changes."""
        snapshot: Snapshot = {}
        roots = [r for r in self._roots if not any(_is_within(r, other) for other in self._roots)]
        for root in roots:
            top = self._base_dir / root if root else self._base_dir
            for dirpath, dirnames, filenames in os.walk(top):
                dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
                for filename in filenames:
                    full = os.path.join(dirpath, filename)
                    rel = Path(full).relative_to(self._base_dir).as_posix()
                    if not any(regex.match(rel) for regex in self._regexes):
                        continue
                    try:
                        stat = os.stat(full)
                    except OSError:
                        continue  # deleted between listing and stat
                    snapshot[rel] = (stat.st_mtime_ns, stat.st_size)
        return snapshot


def _is_within(root: str, other: str) -> bool:
    """Whether `root` is strictly inside `other` (so walking `other` already covers it)."""
    if root == other:
        return False
    return other == "" or root.startswith(other + "/")
