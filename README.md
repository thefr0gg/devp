# devp

A simple, TOML-configured process multiplexer for your terminal — a lighter-weight
alternative to [mprocs](https://github.com/pvolok/mprocs) and
[procmux](https://github.com/napisani/procmux). Define the processes your project
needs in a `devp.toml` file, then run `devp` to see them all in one terminal window:
a sidebar to switch between them, live scrolling output, and keys to start, stop, and
restart each one.

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

| Field       | Required | Type                 | Default          | Description                                                      |
|-------------|----------|----------------------|------------------|-------------------------------------------------------------------|
| `name`      | yes      | string               | —                | Unique name shown in the sidebar.                                 |
| `command`   | yes      | string or list       | —                | See below.                                                         |
| `cwd`       | no       | string               | launch directory | Working directory the process runs in.                            |
| `env`       | no       | table of string→string | `{}`           | Extra environment variables, merged on top of your existing ones. |
| `autostart` | no       | boolean              | `true`           | Whether the process starts automatically when devp launches.      |

**`command` as a string** runs through your shell — use this for anything with
pipes, `&&`, or shell-specific syntax (`npm run dev`, `uvicorn app:app --reload`).

**`command` as a list of strings** runs the executable directly, with no shell in
between — slightly faster, and immune to shell quoting surprises:

```toml
command = ["python", "worker.py", "--verbose"]
```

If `devp.toml` is missing or invalid, devp prints a clear message describing what's
wrong (and where) instead of a stack trace, and exits without launching the TUI.

## Using the TUI

| Key         | Action                                  |
|-------------|------------------------------------------|
| `↑` / `↓`   | Move between processes in the sidebar    |
| `s`         | Start the selected process                |
| `x`         | Stop the selected process                 |
| `r`         | Restart the selected process              |
| `q` / `Ctrl+C` | Quit devp (stops every running process first) |

Each process in the sidebar shows a status glyph:

- `●` green — running
- `○` grey — stopped
- `○` yellow — stopping
- `✕` red — crashed or failed to start

Selecting a process shows its full scrollback in the log pane on the right, and new
output keeps streaming in live. If a process crashes or fails to start, you'll see
an error toast explaining why, in addition to the sidebar turning red.

When you quit devp — with `q` or `Ctrl+C` — every process it started is stopped
first, so nothing keeps running in the background after you close the terminal.

## Development

```bash
poetry install
poetry run pytest      # run the test suite
poetry run devp         # run the app (needs a devp.toml in the cwd)
```

The codebase is small and split by responsibility:

- `devp/config.py` — parses and validates `devp.toml`
- `devp/process.py` — spawns, monitors, and controls subprocesses
- `devp/app.py` — the Textual TUI (sidebar, log pane, keybindings)
- `devp/messages.py` / `devp/widgets.py` — small supporting pieces for the TUI
- `devp/__main__.py` — the `devp` command's entry point

## Known limitations (v1)

- No autorestart on crash, dependency ordering between processes, or log
  search/filtering yet.
- On Windows, stopping a process is always an immediate hard kill — there's no
  graceful-shutdown signal equivalent to SIGTERM for arbitrary child processes.

## License

[MIT](LICENSE)
