# devp

A simple, TOML-configured process multiplexer for your terminal — a lighter-weight
alternative to [mprocs](https://github.com/pvolok/mprocs) and
[procmux](https://github.com/napisani/procmux). Define the processes (and cron jobs)
your project needs in a `devp.toml` file, then run `devp` to see them all in one
terminal window: a sidebar to switch between them, live scrolling output, and keys
to start, stop, and restart each one.

## Install

Requires Python 3.11+ and [Poetry](https://python-poetry.org/).

```bash
git clone <this repo>
cd devp
poetry install
```

## Quick start

Create a `devp.toml` in your project's root:

```toml
[[process]]
name = "api"
command = "uvicorn app:app --reload"

[[process]]
name = "worker"
command = ["python", "worker.py"]
cwd = "backend"

[[process]]
name = "frontend"
command = "npm run dev"
cwd = "frontend"
autostart = false
```

Then, from that same directory, run:

```bash
poetry run devp
```

You'll see a sidebar listing `api`, `worker`, and `frontend`. `api` and `worker`
start automatically; `frontend` waits until you start it yourself.

## Configuring `devp.toml`

Each process is a `[[process]]` entry:

| Field         | Required | Type                    | Default          | Description                                                      |
|---------------|----------|-------------------------|------------------|-------------------------------------------------------------------|
| `name`        | yes      | string                  | —                | Unique name shown in the sidebar.                                  |
| `command`     | yes      | string or list          | —                | See below.                                                         |
| `cwd`         | no       | string                  | launch directory | Working directory the process runs in.                            |
| `env`         | no       | table of string→string  | `{}`             | Extra environment variables, merged on top of your existing ones. |
| `autostart`   | no       | boolean                 | `true`           | Whether the process starts automatically when devp launches.      |
| `autorestart` | no       | boolean                 | `false`          | Automatically restart the process if it crashes (see below).      |
| `depends_on`  | no       | list of strings         | `[]`             | Other process/cron names that must be running first (see below).  |

**`command` as a string** runs through your shell — use this for anything with
pipes, `&&`, or shell-specific syntax (`npm run dev`, `uvicorn app:app --reload`).

**`command` as a list of strings** runs the executable directly, with no shell in
between — slightly faster, and immune to shell quoting surprises:

```toml
command = ["python", "worker.py", "--verbose"]
```

If `devp.toml` is missing or invalid, devp prints a clear message describing what's
wrong (and where) instead of a stack trace, and exits without launching the TUI.

### Restarting automatically on crash

Set `autorestart = true` on a process to have devp bring it back on its own after it
exits with a non-zero code:

```toml
[[process]]
name = "worker"
command = "python worker.py"
autorestart = true
```

Restarts back off exponentially on repeated crashes (1s, 2s, 4s, ... capped at 30s),
resetting once the process runs to a clean exit, so a persistently broken command
doesn't spin in a tight loop. Pressing `x` to stop it cancels any restart that's
pending, so a deliberate stop always sticks.

### Ordering startup and shutdown with `depends_on`

Give a process or cron job a `depends_on` list of other names to make sure those
start first:

```toml
[[process]]
name = "db"
command = "docker run --rm -p 5432:5432 postgres"

[[process]]
name = "api"
command = "uvicorn app:app --reload"
depends_on = ["db"]
```

On launch, `api` won't start until `db` has been started. On quit, the order is
reversed — `api` stops before `db` does. devp validates the dependency graph when it
loads `devp.toml`: an unknown name, a dependency on something that doesn't autostart
(it would never actually satisfy the dependency), or a circular `depends_on` are all
rejected up front with a clear error, rather than surfacing as a runtime hang.

### Cron jobs

A `[[cron]]` entry runs a command on a recurring schedule instead of continuously,
using standard 5-field cron syntax (minute hour day-of-month month day-of-week):

```toml
[[cron]]
name = "backup"
command = "python backup.py"
schedule = "0 * * * *"   # every hour, on the hour
```

| Field       | Required | Type                 | Default          | Description                                                      |
|-------------|----------|----------------------|------------------|-------------------------------------------------------------------|
| `name`      | yes      | string               | —                | Unique name shown in the sidebar (shared with `[[process]]` names). |
| `command`   | yes      | string or list       | —                | Same rules as a process's `command` (see above).                   |
| `schedule`  | yes      | string               | —                | A standard 5-field cron expression.                                |
| `cwd`       | no       | string               | launch directory | Working directory each run uses.                                  |
| `env`       | no       | table of string→string | `{}`           | Extra environment variables for each run.                          |
| `enabled`   | no       | boolean              | `true`           | Whether the schedule is active on launch (like a process's `autostart`). |
| `depends_on`| no       | list of strings      | `[]`             | Other process/cron names that must be running first (see above).  |

Each run's output is appended to the job's log, with a separator line marking where
it started, so you can scroll back through the history of previous runs. If a
scheduled run is still executing when the next scheduled time arrives, that tick is
skipped — runs never overlap.

## Using the TUI

| Key            | Action                                                    |
|----------------|-------------------------------------------------------------|
| `↑` / `↓`      | Move between processes in the sidebar                        |
| `Tab`          | Move focus between the sidebar and the log pane               |
| `s`            | Run the selected process/cron job now                          |
| `x`            | Stop the selected process/cron job's current run                |
| `r`            | Restart the selected process/cron job (stop, then run again)    |
| `/`            | Search the selected process's log                              |
| `n` / `N`      | Jump to the next / previous search match                       |
| `Esc`          | Close the search bar and clear highlighting                    |
| `Ctrl+C`       | Copy the selected log text; with nothing selected, quit        |
| `q`            | Quit devp (stops every running process and cron schedule first) |

With the mouse, click a process to select it, and **double-click** it to run it
(the same as pressing `s`).

devp uses the [Rosé Pine](https://rosepinetheme.com/) color theme. Each item in the
sidebar shows a status glyph. They're plain braille/text characters (not emoji), so
they line up in any terminal font; active states animate:

- `⠋` foam spinner — running
- `⠶` muted — stopped
- `⠋` gold spinner — stopping
- `✗` love (red) — crashed or failed to start
- `⠁` iris orbiting dot — a cron job, enabled and waiting for its next run (shown
  with a live countdown that ticks down every second, e.g. `⠁ backup (next in 5m12s)`)

Pressing `s` on a cron job triggers an immediate one-off run, independent of its
schedule — handy for testing a job without waiting for it to come due.

### Scrolling and searching logs

Press `Tab` to move focus into the log pane, then use the arrow keys, `Page Up` /
`Page Down`, or `Home` / `End` to scroll through a process's full scrollback.

Press `/` to open a search bar at the bottom of the log pane. Type a substring and
press `Enter` — every matching line is highlighted and the view jumps to the first
match. Press `n` / `N` to cycle to the next / previous match, and `Esc` to close the
search bar and clear the highlighting. Searching (and scrolling) always applies to
the currently selected process's log.

### Selecting and copying log text

Drag with the mouse across the log pane to select text; the selection can span
several lines, and a long line that's wrapped on screen is copied as the single
line the process printed. Press `Ctrl+C` to copy it; a toast confirms how much was
copied.

devp copies through your terminal (OSC 52, supported by most modern terminals) and,
where one is installed, your system's clipboard tool (`pbcopy`, `clip`, `wl-copy`,
`xclip`, or `xsel`), so copying works even in terminals without OSC 52.

### Process output

Selecting a process shows its full scrollback in the log pane on the right, and new
output keeps streaming in live. If a process crashes or fails to start, you'll see
an error toast explaining why, in addition to the sidebar turning red.

When you quit devp — with `q`, or `Ctrl+C` with nothing selected — every process it started is stopped
first, so nothing keeps running in the background after you close the terminal.

## Development

```bash
poetry install
poetry run pytest      # run the test suite
poetry run devp         # run the app (needs a devp.toml in the cwd)
```

The codebase is small and split by responsibility:

- `devp/config.py` — parses and validates `devp.toml`
- `devp/process.py` — spawns, monitors, and controls a single subprocess
- `devp/cron.py` — runs a process on a recurring schedule, reusing `devp/process.py`
- `devp/manager.py` — builds and coordinates every configured process and cron job
- `devp/app.py` — the Textual TUI (sidebar, log pane, keybindings)
- `devp/log_view.py` — the log pane: wraps and renders only visible rows, handles selection
- `devp/clipboard.py` — copies to the system clipboard with the platform's native tool
- `devp/messages.py` / `devp/widgets.py` — small supporting pieces for the TUI
- `devp/__main__.py` — the `devp` command's entry point

## Known limitations

- On Windows, a graceful stop needs a real console attached to deliver
  `CTRL_BREAK_EVENT` (true for a normal interactive terminal session). If none is
  attached — some task runners and detached-service contexts — devp skips straight
  to a hard kill rather than waiting out the stop timeout for a signal that could
  never arrive. Even with a console, `CTRL_BREAK_EVENT` only works for processes
  that handle it (Python, Node, and most console apps do); an unresponsive process
  still gets a hard kill once the stop timeout elapses.
- `autorestart`'s crash-loop backoff resets only on a clean exit, not after a
  crashy process has simply stayed up for a while — a process crashing once an hour
  will still see its backoff climb toward the 30s cap over time.
- Log search/filtering across multiple processes at once isn't supported — search
  always applies to whichever process's log is currently selected.

## License

[MIT](LICENSE)
