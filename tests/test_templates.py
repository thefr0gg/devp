import json
import tomllib

import pytest

from devp.__main__ import STARTER_CONFIG, main
from devp.config import load_config
from devp.templates import (
    TEMPLATES,
    detect,
    detect_js_manager,
    detect_python_runner,
    render,
    script_command,
)


def package_json(path, deps=None, scripts=None, **extra):
    data = {"dependencies": deps or {}, "scripts": scripts or {}, **extra}
    (path / "package.json").write_text(json.dumps(data))


@pytest.mark.parametrize(
    "lockfile, expected",
    [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("package-lock.json", "npm"),
        (None, "npm"),
    ],
)
def test_js_manager_is_detected_from_the_lockfile(tmp_path, lockfile, expected):
    package_json(tmp_path)
    if lockfile:
        (tmp_path / lockfile).write_text("")
    assert detect_js_manager(tmp_path) == expected


def test_package_manager_field_beats_the_lockfile(tmp_path):
    package_json(tmp_path, packageManager="pnpm@9.1.0")
    (tmp_path / "yarn.lock").write_text("")
    assert detect_js_manager(tmp_path) == "pnpm"


def test_unknown_package_manager_field_falls_back_to_the_lockfile(tmp_path):
    package_json(tmp_path, packageManager="somethingelse@1")
    (tmp_path / "yarn.lock").write_text("")
    assert detect_js_manager(tmp_path) == "yarn"


@pytest.mark.parametrize(
    "manager, expected",
    [("npm", "npm run dev"), ("bun", "bun run dev"), ("yarn", "yarn dev"), ("pnpm", "pnpm dev")],
)
def test_script_commands_per_manager(manager, expected):
    assert script_command(manager, "dev") == expected


@pytest.mark.parametrize(
    "deps, expected",
    [
        ({"next": "14", "react": "18"}, "next"),
        ({"react": "18", "vite": "5"}, "react"),
        ({"react-scripts": "5"}, "react"),
        ({"vue": "3"}, "vue"),
        ({"express": "4"}, "node"),
        ({}, "node"),
    ],
)
def test_javascript_projects_are_recognized(tmp_path, deps, expected):
    package_json(tmp_path, deps)
    assert detect(tmp_path) == expected


@pytest.mark.parametrize(
    "files, expected",
    [
        ({"manage.py": ""}, "django"),
        ({"requirements.txt": "FastAPI==0.1\nuvicorn"}, "fastapi"),
        ({"pyproject.toml": '[project]\ndependencies = ["flask"]'}, "flask"),
        ({"requirements.txt": "requests"}, "python"),
        ({"go.mod": "module x"}, "go"),
        ({"Cargo.toml": "[package]"}, "rust"),
        ({"compose.yaml": "services: {}"}, "docker"),
    ],
)
def test_other_projects_are_recognized(tmp_path, files, expected):
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    assert detect(tmp_path) == expected


def test_an_empty_directory_is_not_recognized(tmp_path):
    assert detect(tmp_path) is None


def test_subfolders_with_projects_make_a_workspace(tmp_path):
    (tmp_path / "web").mkdir()
    package_json(tmp_path / "web", {"react": "18"})
    (tmp_path / "node_modules").mkdir()
    package_json(tmp_path / "node_modules", {"react": "18"})  # never counted
    assert detect(tmp_path) == "workspace"


def test_python_runner_detection(tmp_path):
    assert detect_python_runner(tmp_path) == ("", None)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("")
    assert detect_python_runner(tmp_path) == ("", ".venv")
    (tmp_path / "Pipfile").write_text("")
    assert detect_python_runner(tmp_path)[0] == "pipenv run "
    (tmp_path / "uv.lock").write_text("")
    assert detect_python_runner(tmp_path)[0] == "uv run "
    (tmp_path / "poetry.lock").write_text("")
    assert detect_python_runner(tmp_path)[0] == "poetry run "


def test_react_template_uses_the_detected_manager_and_script(tmp_path):
    package_json(tmp_path, {"react-scripts": "5"}, {"start": "react-scripts start"})
    (tmp_path / "yarn.lock").write_text("")
    entry = tomllib.loads(render("react", tmp_path))["process"][0]
    assert entry["command"] == "yarn start"
    assert entry["ready_port"] == 3000


def test_vite_react_uses_the_dev_script_and_vite_port(tmp_path):
    package_json(tmp_path, {"react": "18"}, {"dev": "vite"})
    entry = tomllib.loads(render("react", tmp_path))["process"][0]
    assert entry["command"] == "npm run dev" and entry["ready_port"] == 5173


def test_package_manager_override_wins(tmp_path):
    package_json(tmp_path, {"next": "14"}, {"dev": "next dev"})
    (tmp_path / "yarn.lock").write_text("")
    entry = tomllib.loads(render("next", tmp_path, js_manager="bun"))["process"][0]
    assert entry["command"] == "bun run dev"


def test_fastapi_template_finds_the_app_module_and_runner(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("")
    (tmp_path / "poetry.lock").write_text("")
    entry = tomllib.loads(render("fastapi", tmp_path))["process"][0]
    assert entry["command"] == "poetry run uvicorn app.main:app --reload"
    assert entry["ready_port"] == 8000 and "venv" not in entry


def test_python_template_activates_an_existing_venv(tmp_path):
    (tmp_path / "main.py").write_text("")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("")
    entry = tomllib.loads(render("python", tmp_path))["process"][0]
    assert entry["command"] == "python main.py" and entry["venv"] == ".venv"


def test_workspace_template_gives_each_project_its_own_cwd_and_name(tmp_path):
    (tmp_path / "frontend").mkdir()
    package_json(tmp_path / "frontend", {"react": "18"}, {"dev": "vite"})
    (tmp_path / "frontend" / "pnpm-lock.yaml").write_text("")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi")
    (tmp_path / "backend" / "main.py").write_text("")
    entries = tomllib.loads(render("workspace", tmp_path))["process"]
    by_name = {e["name"]: e for e in entries}
    assert by_name["frontend-web"]["cwd"] == "frontend"
    assert by_name["frontend-web"]["command"] == "pnpm dev"  # each subfolder's own manager
    assert by_name["backend-api"]["cwd"] == "backend"


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_template_renders_a_config_that_loads(tmp_path, name):
    package_json(tmp_path, {"react": "18"}, {"dev": "vite"})
    (tmp_path / "main.py").write_text("")
    (tmp_path / "web").mkdir()
    package_json(tmp_path / "web", {"vue": "3"})
    # Written through the CLI so the header and layout are exercised too.
    import os

    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        main(["init", "--template", name])
        config = load_config(tmp_path / "devp.toml")
    finally:
        os.chdir(old)
    assert config.processes


def test_init_detects_the_project_and_reports_it(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    package_json(tmp_path, {"next": "14"}, {"dev": "next dev"})
    (tmp_path / "pnpm-lock.yaml").write_text("")
    main(["init"])
    out = capsys.readouterr().out
    assert "next" in out and "detected" in out and "pnpm" in out
    assert load_config(tmp_path / "devp.toml").processes[0].command == "pnpm dev"


def test_init_in_an_unrecognized_directory_writes_the_generic_starter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    assert (tmp_path / "devp.toml").read_text() == STARTER_CONFIG


def test_init_template_flag_overrides_detection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    package_json(tmp_path, {"next": "14"})
    main(["init", "-t", "go"])
    assert load_config(tmp_path / "devp.toml").processes[0].command == "go run ."


def test_init_package_manager_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    package_json(tmp_path, {"react": "18"}, {"dev": "vite"})
    main(["init", "-p", "pnpm"])
    assert load_config(tmp_path / "devp.toml").processes[0].command == "pnpm dev"


def test_init_rejects_unknown_templates_and_managers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for args in (["init", "-t", "cobol"], ["init", "-p", "nuget"]):
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 2
    assert not (tmp_path / "devp.toml").exists()


def test_init_list_shows_every_template_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["init", "--list"])
    out = capsys.readouterr().out
    assert all(name in out for name in TEMPLATES)
    assert not (tmp_path / "devp.toml").exists()


def test_init_template_still_refuses_to_overwrite_without_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "devp.toml").write_text("keep me")
    with pytest.raises(SystemExit):
        main(["init", "-t", "go"])
    assert (tmp_path / "devp.toml").read_text() == "keep me"
    main(["init", "-t", "go", "--force"])
    assert "go run" in (tmp_path / "devp.toml").read_text()


# ── the interactive menu ─────────────────────────────────────────────────────

SAMPLE = {"generic": "a minimal example", "react": "React", "go": "Go", "rust": "Rust"}


def pick(*keys, suggested=None, width=80):
    """Run the menu with a scripted sequence of key presses."""
    import io

    from rich.console import Console

    from devp.picker import choose_template

    presses = iter(keys)
    console = Console(file=io.StringIO(), width=width)
    return choose_template(SAMPLE, suggested, key_source=lambda: next(presses), console=console)


def test_arrows_move_and_enter_selects():
    assert pick("enter") == "generic"  # nothing suggested: the first entry
    assert pick("down", "down", "enter") == "go"
    assert pick("down", "down", "up", "enter") == "react"


def test_navigation_wraps_around():
    assert pick("up", "enter") == "rust"
    assert pick("down", "down", "down", "down", "enter") == "generic"


def test_vim_keys_also_move():
    assert pick("j", "j", "j", "k", "enter") == "go"


def test_unrelated_keys_are_ignored():
    assert pick("x", "5", "", "enter") == "generic"


def test_the_suggested_template_starts_highlighted():
    assert pick("enter", suggested="rust") == "rust"
    assert pick("up", "enter", suggested="rust") == "go"
    assert pick("enter", suggested="not-a-template") == "generic"


@pytest.mark.parametrize("key", ["escape", "q", "ctrl-c"])
def test_cancel_keys_return_no_choice(key):
    assert pick("down", key) is None


def test_an_interrupt_while_waiting_cancels():
    from rich.console import Console

    from devp.picker import choose_template

    def interrupted():
        raise KeyboardInterrupt

    import io

    console = Console(file=io.StringIO())
    assert choose_template(SAMPLE, key_source=interrupted, console=console) is None


def test_escape_sequences_decode_to_arrows():
    from devp.picker import decode_escape

    assert decode_escape("[A") == "up" and decode_escape("OA") == "up"
    assert decode_escape("[B") == "down" and decode_escape("OB") == "down"
    assert decode_escape("") == "escape"  # a lone Esc key
    assert decode_escape("[C") == "escape"  # other keys (right arrow, ...) do nothing useful


def test_the_menu_lists_every_template_with_the_highlight_marked():
    import io

    from rich.console import Console

    from devp.picker import _render

    console = Console(file=io.StringIO(), width=80, force_terminal=False)
    console.print(_render(SAMPLE, 1, fancy=False))
    text = console.file.getvalue()
    assert all(name in text for name in SAMPLE)
    assert "> react" in text and "> go" not in text
    assert "up/down to move" in text


def test_interactive_init_writes_the_chosen_template(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_choose(templates, suggested):
        seen["names"], seen["suggested"] = list(templates), suggested
        return "go"

    monkeypatch.setattr("devp.__main__._is_interactive", lambda: True)
    monkeypatch.setattr("devp.picker.choose_template", fake_choose)
    package_json(tmp_path, {"next": "14"}, {"dev": "next dev"})

    main(["init"])

    assert seen["names"] == list(TEMPLATES) and seen["suggested"] == "next"
    assert load_config(tmp_path / "devp.toml").processes[0].command == "go run ."


def test_interactive_init_does_not_write_when_cancelled(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("devp.__main__._is_interactive", lambda: True)
    monkeypatch.setattr("devp.picker.choose_template", lambda templates, suggested: None)
    with pytest.raises(SystemExit) as exc:
        main(["init"])
    assert exc.value.code == 1
    assert not (tmp_path / "devp.toml").exists()
    assert "Cancelled" in capsys.readouterr().out


def test_template_flag_skips_the_menu(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("devp.__main__._is_interactive", lambda: True)

    def no_menu(templates, suggested):
        raise AssertionError("the menu must not open when --template is given")

    monkeypatch.setattr("devp.picker.choose_template", no_menu)
    main(["init", "-t", "rust"])
    assert load_config(tmp_path / "devp.toml").processes[0].command == "cargo run"


def test_without_a_terminal_init_falls_back_to_detection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("devp.__main__._is_interactive", lambda: False)
    (tmp_path / "go.mod").write_text("module x")
    main(["init"])
    assert load_config(tmp_path / "devp.toml").processes[0].command == "go run ."
