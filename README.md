<p align="center">
  <img src="assets/mascot.gif" alt="devp's mascot: a pixel-art bunny in a top hat, swinging a magic wand that throws red sparks, then jumping and twirling it" width="240">
</p>

# devp

A simple, TOML-configured process multiplexer for your terminal — a lighter-weight
alternative to [mprocs](https://github.com/pvolok/mprocs) and
[procmux](https://github.com/napisani/procmux). Define the processes (and cron jobs)
your project needs in a `devp.toml` file, then run `devp` to see them all in one
terminal window: a sidebar to switch between them, live scrolling output, and keys
to start, stop, and restart each one.

Beyond the TUI, devp can generate a config for your project type
([`devp init`](#starting-from-a-template)), be driven by scripts and AI agents through
a [local API](#controlling-devp-from-other-programs), and be used from a
[browser](#using-devp-in-a-browser).

## Install

Requires Python 3.11+ and [Poetry](https://python-poetry.org/).

```bash
git clone <this repo>
cd devp
poetry install
```

## Quick start

Run `devp init` in your project's root to create a starter `devp.toml`, or write
one yourself:

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

Then run:

```bash
poetry run devp
```

You'll see a sidebar listing `api`, `worker`, and `frontend`. `api` and `worker`
start automatically; `frontend` waits until you start it yourself.

### Starting from a template

`devp init` shows a menu of templates right in your terminal, drawn in place and erased
once you choose (not a full-screen UI). Pick one with the arrow keys (or `j`/`k`) and
press Enter; Esc cancels, and nothing is written. The template that looks like your
project, judged from the files in the current directory, starts highlighted, so
usually it's just Enter. The templates:

| Template    | Detected from                                   | Runs                                  |
|-------------|--------------------------------------------------|----------------------------------------|
| `react`     | `react`, `react-dom` or `react-scripts` in `package.json` | the `dev` (or `start`) script, port 5173 (Vite) or 3000 (CRA) |
| `next`      | `next` in `package.json`                         | the `dev` script, port 3000            |
| `vue`       | `vue` or `nuxt` in `package.json`                | the `dev` (or `serve`) script          |
| `node`      | any other `package.json`                         | the `dev`, `start` or `serve` script   |
| `fastapi`   | `fastapi` in `pyproject.toml`, `requirements.txt`, ... | `uvicorn <main:app> --reload`    |
| `flask`     | `flask` in the same files                        | `flask run --debug`                    |
| `django`    | `manage.py`                                      | `python manage.py runserver`           |
| `python`    | other Python projects                            | `main.py` / `app.py` / `python -m pkg` |
| `go`        | `go.mod`                                         | `go run .`, restarting on `.go` changes |
| `rust`      | `Cargo.toml`                                     | `cargo run`                            |
| `docker`    | a Compose file                                   | `docker compose up`                    |
| `workspace` | a monorepo: subfolders that are projects         | one entry per subfolder, with its own `cwd` |
| `generic`   | nothing recognized                               | a minimal example to edit              |

JavaScript commands use your package manager: `packageManager` in `package.json` wins,
then the lockfile (`pnpm-lock.yaml` → pnpm, `yarn.lock` → yarn, `bun.lock`/`bun.lockb`
→ bun, `package-lock.json` → npm), defaulting to npm. Python commands use `poetry run`,
`uv run` or `pipenv run` when the project uses them, or set `venv` when there's a
`.venv`/`venv` folder. In a monorepo each subfolder is detected on its own.

```text
devp init                      choose a template from the menu
devp init -t fastapi           skip the menu and use this template
devp init -p pnpm              force a package manager for JavaScript templates
devp init --list               print every template
```

Without a terminal to show the menu (a script or CI), `devp init` uses the template
that looks like your project instead, or the generic starter if none does.

Always read the result before running it; detection is a good guess, not a promise.

### Command line

```text
devp                      run the nearest devp.toml (here or in a parent directory)
devp -c path/to/file.toml run a specific config file
devp init [-t NAME] [-p PM] [--force]
                          create a devp.toml here, from a template matched to the
                          project (--force overwrites one; --list shows templates)
devp upgrade-config       record this devp's version and config layout in devp.toml
devp ctl ACTION [NAME]    control a running devp (see "Controlling devp from other programs")
devp --no-api             run without the local control API
devp --web [--port N]     serve the TUI to a browser instead (see "Using devp in a browser")
devp --version            print the version
```

Like `git`, devp looks for `devp.toml` in the current directory and then each parent
directory, so you can launch it from anywhere inside your project. It always runs
from the directory that contains the config, so relative `cwd` and `watch` paths
mean the same thing wherever you start it from.

## Configuring `devp.toml`

### Config versions

Like `poetry.lock`, `devp.toml` records which versions it was written for, in a
`[devp]` table that `devp init` creates:

```toml
[devp]
version = "0.3.0"        # devp version that generated this file
config-version = "1.2"   # config layout version; devp warns when it doesn't match
```

`config-version` tracks the *layout* of the file, separately from devp's own
version: its minor part goes up when the layout changes in a backward-compatible way
(a new optional setting), and its major part when it changes in a breaking way (a
renamed or removed setting). When devp loads a config it compares the two:

| The file's `config-version` is…            | devp…                                              |
|--------------------------------------------|----------------------------------------------------|
| the same                                   | runs it                                            |
| missing (a file from before versioning)    | runs it, with a warning                            |
| an older or newer minor version (`1.x`)    | runs it, with a warning                            |
| a different major version                  | refuses it, explaining what to update              |

devp also warns when the file was generated by a newer devp than the one running.
Warnings appear as notifications when devp starts or reloads the config. Once
you've checked a file against the current layout, run `devp upgrade-config` to
record the current versions; it only rewrites the `[devp]` table (adding it if
needed) and leaves the rest of the file, comments included, as it was.

Each process is a `[[process]]` entry:

| Field         | Required | Type                    | Default          | Description                                                      |
|---------------|----------|-------------------------|------------------|-------------------------------------------------------------------|
| `name`        | yes      | string                  | —                | Unique name shown in the sidebar.                                  |
| `command`     | yes      | string or list          | —                | See below.                                                         |
| `cwd`         | no       | string                  | launch directory | Working directory the process runs in.                            |
| `env`         | no       | table of string→string  | `{}`             | Extra environment variables, merged on top of your existing ones. |
| `venv`        | no       | string                  | —                | Virtualenv directory to activate: its `bin`/`Scripts` goes first on `PATH` and `VIRTUAL_ENV` is set. Relative to the config's directory. |
| `autostart`   | no       | boolean                 | `true`           | Whether the process starts automatically when devp launches.      |
| `autorestart` | no       | boolean                 | `false`          | Automatically restart the process if it crashes (see below).      |
| `depends_on`  | no       | list of strings         | `[]`             | Other process/cron names that must be running first (see below).  |
| `shell`       | no       | string or list          | system default   | Shell that runs a string `command` (see below).                   |

**`command` as a string** runs through your shell — use this for anything with
pipes, `&&`, or shell-specific syntax (`npm run dev`, `uvicorn app:app --reload`).

**`command` as a list of strings** runs the executable directly, with no shell in
between — slightly faster, and immune to shell quoting surprises:

```toml
command = ["python", "worker.py", "--verbose"]
```

#### Environment variables and virtualenvs

`env` sets variables for one process or cron job, on top of devp's own environment
(an `env` value wins over an inherited one). `venv` points at a Python virtualenv
directory and activates it the way its `activate` script would: the environment's
`bin` (`Scripts` on Windows) goes first on `PATH`, `VIRTUAL_ENV` is set, and
`PYTHONHOME` is dropped. No `source .venv/bin/activate &&` needed, and it works with
list commands too. Both fields work on `[[process]]` and `[[cron]]` entries.

```toml
[[process]]
name = "api"
command = "uvicorn app:app --reload"
cwd = "backend"
venv = "backend/.venv"         # relative to devp.toml's directory, like cwd
env = { DEBUG = "1", DATABASE_URL = "sqlite:///dev.db" }
```

Values in `env` must be strings. When both are set, `venv` is applied last, so it
decides `PATH`'s first entry and `VIRTUAL_ENV`.

#### Choosing the shell

String commands run in the system's default shell (`sh`, or `cmd` on Windows) unless
you pick one, per process or cron job with `shell`, or for everything in a
`[defaults]` table (a process's own `shell` wins):

```toml
[defaults]
shell = "pwsh"                 # every string command runs in PowerShell 7

[[process]]
name = "api"
command = "source scripts/env.sh && uvicorn app:app --reload"
shell = "bash"                 # this one needs bash
```

Give a shell's name (found on `PATH`) or its full path. devp passes the command the
way each shell expects: `-c` for `bash`, `zsh`, `fish`, `sh` and other POSIX-style
shells, and `-NoLogo -NoProfile -Command` for `pwsh` / `powershell`. For anything
else, use a list: devp appends the command to it as the last argument, e.g.
`shell = ["bash", "-lc"]` for a login shell, or `["pwsh", "-Command"]` to load your
PowerShell profile. If the shell isn't installed, that process fails to start with
an error saying so. `shell` has no effect on list commands, which never use a shell.

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
so a persistently broken command doesn't spin in a tight loop. The backoff starts
over after a clean exit, or when a crash comes after at least 30s of uptime — a
process that crashes once an hour is restarted after 1s, not 30s. Pressing `x` to stop it cancels any restart that's
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

#### Waiting until a dependency is ready

By default a dependency counts as started as soon as its process has spawned, which
is often before it can actually accept work. Add a readiness check to make its
dependents wait until it's really up:

```toml
[[process]]
name = "db"
command = "docker run --rm -p 5432:5432 postgres"
ready_port = 5432            # ready once localhost:5432 accepts connections

[[process]]
name = "api"
command = "uvicorn app:app --reload"
depends_on = ["db"]
ready_when = "Application startup complete"   # ready once a line matches this regex
ready_timeout = 30           # seconds; default 60
```

| Key             | Type    | Default | Description                                                        |
|-----------------|---------|---------|--------------------------------------------------------------------|
| `ready_when`    | string  | —       | A regular expression; ready once an output line matches it (color codes are ignored). |
| `ready_port`    | integer | —       | Ready once something accepts TCP connections on this port on localhost. |
| `ready_timeout` | number  | `60`    | How long to wait for the check before giving up.                   |

Use one of `ready_when` or `ready_port`, not both. Until its check passes, a process
shows a rose spinner in the sidebar (“starting”), then switches to the usual running
spinner with an “is ready” toast. If it exits before becoming ready, or the timeout
passes, the processes that depend on it are left stopped, with a note in their log
explaining why. You can still start them yourself with `s`.

### Restarting on file changes

Give a process a `watch` list of glob patterns and devp restarts it whenever a
matching file is added, changed, or deleted, like `nodemon`:

```toml
[[process]]
name = "worker"
command = "python worker.py"
watch = ["src/**/*.py", "config/*.yaml"]
```

Patterns are relative to the process's `cwd` (or the directory devp runs in). `*`
and `?` match within one directory level and `**` matches any number of levels, so
`src/**/*.py` covers `src/app.py` as well as `src/pkg/mod.py`. `.git`,
`node_modules`, `__pycache__`, virtualenvs, and tool caches are always skipped.

devp checks for changes about once a second, logs which file changed
(`--- src/app.py changed, restarting ---`), and restarts the process. It only
restarts a process that's running, starting, or crashed; if you stopped it with `x`,
it stays stopped until you start it again.

### Reloading the config

devp notices when you save `devp.toml` while it's running and asks whether to
reload it. Choosing **Stop & reload** (`y`) stops everything, rebuilds the process
list from the new file, and starts it up again; **Keep current** (`n`) leaves things
as they are, and devp won't ask again until the next save. If the new file has a
mistake, devp shows the error and keeps running the previous config; a save that
changes nothing that matters (only comments, say) doesn't prompt at all.

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
| `venv`      | no       | string               | —                | Virtualenv directory to activate for each run, as for processes.   |
| `enabled`   | no       | boolean              | `true`           | Whether the schedule is active on launch (like a process's `autostart`). |
| `depends_on`| no       | list of strings      | `[]`             | Other process/cron names that must be running first (see above).  |
| `shell`     | no       | string or list       | system default   | Shell for a string `command`, as for processes (see above).        |

Each run's output is appended to the job's log, with a separator line marking where
it started, so you can scroll back through the history of previous runs. If a
scheduled run is still executing when the next scheduled time arrives, that tick is
skipped — runs never overlap.

## Controlling devp from other programs

While devp runs it serves a small local API, so scripts, editors, and AI agents can
list, start, stop, and restart your processes and read their logs. Everything shows
up live in the TUI, as if you had pressed the keys yourself.

From a shell (any directory inside the project):

```text
devp ctl list                  every process and cron job with its state, pid, restarts
devp ctl status NAME           one entry
devp ctl start NAME            start a process (or run a cron job now)  [--wait-ready]
devp ctl stop NAME
devp ctl restart NAME          [--wait-ready]
devp ctl start-all             in dependency order
devp ctl stop-all              dependents first
devp ctl logs NAME [-n 100]    recent output, ANSI codes stripped
devp ctl quit                  stop everything and exit devp
```

Results are printed as JSON; failures exit non-zero with a message on stderr.

**The protocol.** devp listens on a random port on `127.0.0.1` and writes where, plus
a secret token, to `.devp-api.json` next to `devp.toml` (readable only by you on
POSIX; devp deletes it on exit; add it to your `.gitignore`). Connect over TCP and
send one JSON object per line; you get one JSON line back:

```json
{"id": 1, "token": "<from .devp-api.json>", "method": "start", "params": {"name": "web"}}
{"id": 1, "ok": true, "result": {"name": "web", "kind": "process", "state": "running", "pid": 4242, "exit_code": null, "uptime": 0.1, "restarts": 0, "last_error": null}}
{"id": 2, "ok": false, "error": "no process named 'nope'"}
```

| Method                        | Params                           | Result                                  |
|-------------------------------|----------------------------------|-----------------------------------------|
| `ping`                        | —                                | `{"pong": true, "pid": ...}`            |
| `list`                        | —                                | list of entries                         |
| `status` / `start` / `stop` / `restart` | `name`; `wait_ready` (start/restart) | the entry                   |
| `start_all` / `stop_all`      | —                                | list of entries                         |
| `logs`                        | `name`, `lines` (100), `raw` (false), `since` | `{"name": ..., "lines": [...], "next": N}` |
| `sync`                        | `cursors` (name → line cursor)   | `{"epoch", "entries", "logs"}`: everything in one call |
| `quit`                        | —                                | `{"quitting": true}`, then devp exits   |

`next` is a cursor: pass it back as `since` to receive only lines written after it, once
each, which is how to follow a log. `sync` is what the browser view polls; `epoch`
changes when devp reloads its config, so a client knows its entries are stale.

Cron entries also carry `schedule` and `next_run_at`. Requests without the right
token are rejected, and only processes already in `devp.toml` can be started, so the
API can't be used to run arbitrary commands. Anything that can read the token file
can control devp, so don't put it somewhere shared. Run `devp --no-api` to turn the
API off. If two devp instances run from the same config, the later one's file wins.

## Using devp in a browser

```text
devp --web                    serve on http://localhost:8000
devp --web --port 9000
devp --web --host 0.0.0.0     reachable from other machines (read the warning below)
```

`devp --web` runs your processes without a terminal UI and serves the same TUI to a
browser (via [textual-serve](https://github.com/Textualize/textual-serve)). It prints
a short banner with a URL that includes an access token; open that. Afterwards the
terminal shows one line per event instead of raw HTTP logs: browser tabs connecting
and leaving, processes starting, stopping and failing, config reloads, and blocked
requests (wrong token). The web mode always runs the control API (see
above), so `--web` can't be combined with `--no-api`, and `devp ctl` works alongside it.

The processes belong to the `devp --web` process, not to a browser tab: any number of
tabs show and control the same processes, closing a tab leaves them running, and
`q` in the browser only closes that view. Stop everything with Ctrl+C in the terminal
that runs `devp --web` (or `devp ctl quit`).

**Security.** The web UI can start and stop your processes, so it needs the token
printed at startup (kept in a cookie after the first visit) and binds to `localhost`
by default. Plain HTTP is not encrypted. To use it from another machine, prefer an SSH
tunnel (`ssh -L 8000:localhost:8000 host`) over `--host 0.0.0.0`; if you do bind a
public address, the token travels in clear text unless you add TLS in front of it
(`public_url` links also assume the address you passed to `--host`).

**Editing the config.** Like the terminal UI, `devp --web` watches `devp.toml`. When it's
saved with a meaningful change (comments and whitespace don't count), devp stops every
process and starts the new set, and open browser tabs rebuild their process list on
their own. Because nobody is at the terminal to ask, a changed config is applied
right away instead of after a confirmation prompt. If the new file is invalid, devp
prints the error in its terminal and keeps running the previous config.

## Using the TUI

| Key            | Action                                                    |
|----------------|-------------------------------------------------------------|
| `↑` / `↓`      | Move between processes in the sidebar                        |
| `Tab`          | Move focus between the sidebar and the log pane               |
| `s`            | Run the selected process/cron job now                          |
| `x`            | Stop the selected process/cron job's current run                |
| `r`            | Restart the selected process/cron job (stop, then run again)    |
| `S` / `X`      | Start / stop everything (in `depends_on` order; pauses and resumes cron schedules) |
| `c`            | Clear the log (in the log pane)                                |
| `G`            | Jump to the newest output and follow it again                  |
| `?`            | Show every key and mouse action                                |
| `/`            | Search the selected process's log                              |
| `n` / `N`      | Jump to the next / previous search match                       |
| `Esc`          | Close the search bar and clear highlighting                    |
| `Ctrl+C`       | Copy the selected log text; with nothing selected, quit        |
| `q`            | Quit devp (stops every running process and cron schedule first) |

Keys depend on which pane has focus (`Tab` switches): process controls work in the
process list, `G` / `n` / `N` in the log pane, and so on. The footer always shows
exactly the keys that work where you are; press `?` for the full list.

With the mouse, click a process to select it, and **double-click** it to run it
(the same as pressing `s`).

Each item in the sidebar shows a status glyph: the sparks from the mascot's magic
wand. They're plain text symbols (not emoji), so they line up in any terminal font;
active states animate:

- `✦` pink, gathering — starting (waiting for its `ready_when` / `ready_port` check)
- `✶` cyan, twinkling — running
- `✸` yellow, fading — stopping
- `✧` grey — stopped
- `✗` red — crashed or failed to start
- `✧` purple, twinkling slowly — a cron job, enabled and waiting for its next run
  (shown with a live countdown that ticks down every second, e.g.
  `✧ backup (next in 5m12s)`)

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
output keeps streaming in live, in the colors the process printed it with (color
codes are shown as colors; search and copy work on the plain text).
Emoji, CJK text, box drawing, and Nerd Font glyphs are shown at their real width.
Progress bars and spinners that redraw a line in place (with `\r`) show their final
state, e.g. `[100%] done`, instead of every intermediate frame, and output that
isn't UTF-8 is decoded with the system's legacy encoding on Windows. Python
processes are started with `PYTHONIOENCODING=utf-8` (unless you set it yourself),
so printing an emoji can't crash them on Windows. If a process crashes or fails to start, you'll see
an error toast explaining why, in addition to the sidebar turning red.

When you quit devp — with `q`, or `Ctrl+C` with nothing selected — every process it started is stopped
first, so nothing keeps running in the background after you close the terminal.

## License

[MIT](LICENSE)
