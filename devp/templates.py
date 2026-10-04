"""Starter `devp.toml` templates for `devp init`, with project and package manager detection."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JS_MANAGERS = ("npm", "yarn", "pnpm", "bun")
_SKIP_DIRS = {"node_modules", "venv", "env", "dist", "build", "target", "vendor", "__pycache__"}


@dataclass(frozen=True)
class Project:
    """What `devp init` found in a directory (or was told): used to fill in a template."""

    path: Path
    js_manager: str = "npm"

    @property
    def package_json(self) -> dict[str, Any]:
        return _read_json(self.path / "package.json")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""


def _q(value: str) -> str:
    """A TOML basic string (JSON string escaping is valid TOML)."""
    return json.dumps(value)


# ── package manager detection ────────────────────────────────────────────────


def detect_js_manager(path: Path) -> str:
    """npm, yarn, pnpm, or bun: from `packageManager` in package.json, else the lockfile."""
    declared = _read_json(path / "package.json").get("packageManager")
    if isinstance(declared, str):
        name = declared.split("@", 1)[0]
        if name in JS_MANAGERS:
            return name
    lockfiles = (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("package-lock.json", "npm"),
    )
    for lockfile, manager in lockfiles:
        if (path / lockfile).exists():
            return manager
    return "npm"


def script_command(manager: str, script: str) -> str:
    """The command that runs a package.json script with `manager`."""
    if manager == "npm" or manager == "bun":
        return f"{manager} run {script}"
    return f"{manager} {script}"  # yarn and pnpm run scripts directly


def detect_python_runner(path: Path) -> tuple[str, str | None]:
    """How to run Python tools here: ('poetry run ', None), ('', '.venv'), or ('', None)."""
    pyproject = _read_text(path / "pyproject.toml")
    if (path / "poetry.lock").exists() or "[tool.poetry]" in pyproject:
        return "poetry run ", None
    if (path / "uv.lock").exists() or "[tool.uv]" in pyproject:
        return "uv run ", None
    if (path / "Pipfile").exists():
        return "pipenv run ", None
    for venv in (".venv", "venv"):
        if (path / venv / "pyvenv.cfg").exists():
            return "", venv
    return "", None


# ── project detection ────────────────────────────────────────────────────────


def detect_template(path: Path) -> str | None:
    """The template that suits the project in `path`, or None if nothing is recognized."""
    package = _read_json(path / "package.json")
    if package:
        deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        if "next" in deps:
            return "next"
        if any(name in deps for name in ("react", "react-dom", "react-scripts")):
            return "react"
        if "vue" in deps or "nuxt" in deps:
            return "vue"
        return "node"
    if (path / "manage.py").exists():
        return "django"
    python_files = ("pyproject.toml", "requirements.txt", "Pipfile", "setup.py")
    manifests = " ".join(_read_text(path / name) for name in python_files)
    if "fastapi" in manifests:
        return "fastapi"
    if "flask" in manifests:
        return "flask"
    if any((path / name).exists() for name in python_files):
        return "python"
    if (path / "go.mod").exists():
        return "go"
    if (path / "Cargo.toml").exists():
        return "rust"
    if any((path / n).exists() for n in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")):
        return "docker"
    return None


def _child_projects(path: Path) -> list[tuple[Path, str]]:
    """Immediate subdirectories that are recognizable projects, with their template."""
    found = []
    for child in sorted(path.iterdir()):
        if child.is_dir() and not child.name.startswith(".") and child.name not in _SKIP_DIRS:
            template = detect_template(child)
            if template is not None and template != "docker":
                found.append((child, template))
    return found


def detect(path: Path) -> str | None:
    """`detect_template`, falling back to 'workspace' when subfolders hold the projects."""
    template = detect_template(path)
    if template is not None:
        return template
    return "workspace" if _child_projects(path) else None


# ── entry builders ───────────────────────────────────────────────────────────


def _entry(name: str, command: str, *, cwd: str | None = None, **extra: Any) -> str:
    lines = ["[[process]]", f"name = {_q(name)}", f"command = {_q(command)}"]
    if cwd:
        lines.append(f"cwd = {_q(cwd)}")
    for key, value in extra.items():
        lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines) + "\n"


def _js_script(package: dict[str, Any], *candidates: str) -> str:
    scripts = package.get("scripts", {})
    return next((name for name in candidates if name in scripts), candidates[0])


def _js_entries(project: Project, name: str, port: int, *candidates: str, cwd: str | None = None) -> str:
    script = _js_script(project.package_json, *candidates)
    return _entry(
        name,
        script_command(project.js_manager, script),
        cwd=cwd,
        ready_port=port,
    )


def _vite_or_cra_port(package: dict[str, Any]) -> int:
    deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    return 3000 if "react-scripts" in deps else 5173


def _node(project: Project, cwd: str | None = None) -> str:
    package = project.package_json
    port = 3000 if "express" in {**package.get("dependencies", {})} else None
    script = _js_script(package, "dev", "start", "serve")
    extra = {"ready_port": port} if port else {}
    return _entry("app", script_command(project.js_manager, script), cwd=cwd, **extra)


def _react(project: Project, cwd: str | None = None) -> str:
    port = _vite_or_cra_port(project.package_json)
    return _js_entries(project, "web", port, "dev", "start", cwd=cwd)


def _next(project: Project, cwd: str | None = None) -> str:
    return _js_entries(project, "web", 3000, "dev", "start", cwd=cwd)


def _vue(project: Project, cwd: str | None = None) -> str:
    return _js_entries(project, "web", 5173, "dev", "serve", cwd=cwd)


def _asgi_module(path: Path) -> str:
    for candidate, module in (
        ("main.py", "main:app"),
        ("app.py", "app:app"),
        ("app/main.py", "app.main:app"),
        ("src/main.py", "src.main:app"),
    ):
        if (path / candidate).exists():
            return module
    return "main:app"


def _python_entry(project: Project, name: str, command: str, cwd: str | None, **extra: Any) -> str:
    runner, venv = detect_python_runner(project.path)
    if venv:
        extra["venv"] = venv if cwd is None else f"{cwd}/{venv}"
    return _entry(name, runner + command, cwd=cwd, **extra)


def _fastapi(project: Project, cwd: str | None = None) -> str:
    module = _asgi_module(project.path)
    return _python_entry(
        project, "api", f"uvicorn {module} --reload", cwd, ready_port=8000
    )


def _flask(project: Project, cwd: str | None = None) -> str:
    return _python_entry(project, "api", "flask run --debug", cwd, ready_port=5000)


def _django(project: Project, cwd: str | None = None) -> str:
    return _python_entry(project, "api", "python manage.py runserver", cwd, ready_port=8000)


def _python(project: Project, cwd: str | None = None) -> str:
    for script in ("main.py", "app.py", "run.py"):
        if (project.path / script).exists():
            return _python_entry(project, "app", f"python {script}", cwd)
    package = next((p.name for p in project.path.iterdir() if (p / "__main__.py").exists()), None)
    command = f"python -m {package}" if package else "python main.py"
    return _python_entry(project, "app", command, cwd)


def _go(project: Project, cwd: str | None = None) -> str:
    return _entry("app", "go run .", cwd=cwd, watch=["**/*.go"])


def _rust(project: Project, cwd: str | None = None) -> str:
    return _entry("app", "cargo run", cwd=cwd)


def _docker(project: Project, cwd: str | None = None) -> str:
    return _entry("compose", "docker compose up", cwd=cwd)


def _generic(project: Project, cwd: str | None = None) -> str:
    return _entry("web", "python -m http.server 8000", cwd=cwd, ready_port=8000)


def _workspace(project: Project, cwd: str | None = None) -> str:
    """One set of entries per subfolder that holds a recognizable project."""
    blocks = []
    for child, template in _child_projects(project.path):
        child_project = Project(child, detect_js_manager(child))
        block = TEMPLATES[template].build(child_project, child.name)
        blocks.append(_prefix_names(block, child.name))
    return "\n".join(blocks)


def _prefix_names(block: str, prefix: str) -> str:
    """`name = "web"` becomes `name = "frontend-web"`, keeping subfolder entries distinct."""
    marker = 'name = "'
    lines = [
        f'{marker}{prefix}-{line[len(marker):]}' if line.startswith(marker) else line
        for line in block.splitlines()
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Template:
    description: str
    build: Callable[..., str]


TEMPLATES: dict[str, Template] = {
    "generic": Template("a minimal example to edit", _generic),
    "node": Template("a Node.js app (runs its dev/start script)", _node),
    "react": Template("React with Vite or Create React App", _react),
    "next": Template("a Next.js app", _next),
    "vue": Template("Vue or Nuxt", _vue),
    "python": Template("a Python script or package", _python),
    "fastapi": Template("FastAPI served by uvicorn", _fastapi),
    "flask": Template("a Flask app", _flask),
    "django": Template("a Django project (manage.py runserver)", _django),
    "go": Template("a Go program (go run, restarts on .go changes)", _go),
    "rust": Template("a Rust program (cargo run)", _rust),
    "docker": Template("Docker Compose", _docker),
    "workspace": Template("a monorepo: one entry per project subfolder", _workspace),
}


def render(template: str, path: Path, js_manager: str | None = None) -> str:
    """The `[[process]]` entries for `template`, filled in from the project at `path`."""
    project = Project(path, js_manager or detect_js_manager(path))
    body = TEMPLATES[template].build(project)
    tomllib.loads(body)  # a template must always produce valid TOML
    return body
