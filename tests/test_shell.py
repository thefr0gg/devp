import asyncio
import shutil
import sys

import pytest

import devp.shell as shell_module
from devp.config import ConfigError, ProcessConfig, load_config
from devp.process import ManagedProcess, ProcessState
from devp.shell import ShellNotFoundError, shell_argv

PYTHON_AS_SHELL = [sys.executable, "-c"]  # a "shell" available everywhere the tests run


def test_default_shell_is_left_to_the_system():
    assert shell_argv(None, "echo hi") is None


@pytest.mark.parametrize(
    ("shell", "flags"),
    [
        ("bash", ["-c"]),
        ("/usr/bin/zsh", ["-c"]),
        ("fish", ["-c"]),
        ("pwsh", ["-NoLogo", "-NoProfile", "-Command"]),
        ("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
         ["-NoLogo", "-NoProfile", "-Command"]),
        ("some-new-shell", ["-c"]),  # unknown shells: the POSIX convention
    ],
)
def test_known_shells_get_their_flags(monkeypatch, shell, flags):
    monkeypatch.setattr(shutil, "which", lambda name: f"/resolved/{name}")
    argv = shell_argv(shell, "echo hi")
    assert argv == [f"/resolved/{shell}", *flags, "echo hi"]


def test_a_list_is_used_verbatim(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/resolved/{name}")
    assert shell_argv(["pwsh", "-NoLogo", "-Command"], "Get-Date") == [
        "/resolved/pwsh", "-NoLogo", "-Command", "Get-Date"
    ]


def test_cmd_on_windows_uses_the_system_shell(monkeypatch):
    monkeypatch.setattr(shell_module, "_IS_WINDOWS", True)
    assert shell_argv("cmd.exe", "dir") is None


def test_a_missing_shell_raises(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ShellNotFoundError, match="shell 'pwsh' not found"):
        shell_argv("pwsh", "Get-Date")


@pytest.mark.asyncio
async def test_a_process_runs_its_command_in_the_configured_shell():
    proc = ManagedProcess(
        ProcessConfig(name="p", command="print('hello from the shell')", shell=PYTHON_AS_SHELL)
    )
    await proc.start()
    await proc.wait()
    await proc._pump_task
    assert list(proc.output) == ["hello from the shell"]


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
async def test_bash_by_name():
    proc = ManagedProcess(
        ProcessConfig(name="p", command='echo "$0 ${BASH_VERSINFO[0]:+is bash}"', shell="bash")
    )
    await proc.start()
    await proc.wait()
    await proc._pump_task
    assert list(proc.output)[0].endswith("is bash")


@pytest.mark.asyncio
async def test_a_missing_shell_is_reported_as_a_failed_start():
    errors = []
    proc = ManagedProcess(
        ProcessConfig(name="p", command="echo hi", shell="definitely-not-a-shell"),
        on_error=lambda name, text: errors.append(text),
    )
    await proc.start()
    assert proc.state == ProcessState.CRASHED
    assert errors == ["failed to start: shell 'definitely-not-a-shell' not found"]


def write(tmp_path, text):
    path = tmp_path / "devp.toml"
    path.write_text(text)
    return path


def test_shell_config_per_entry_and_from_defaults(tmp_path):
    config = load_config(
        write(
            tmp_path,
            """
            [defaults]
            shell = "pwsh"

            [[process]]
            name = "uses-default"
            command = "Get-Date"

            [[process]]
            name = "own-shell"
            command = "echo hi"
            shell = ["bash", "-lc"]

            [[process]]
            name = "no-shell"
            command = ["python", "app.py"]

            [[cron]]
            name = "job"
            command = "echo tick"
            schedule = "* * * * *"
            shell = "zsh"
            """,
        )
    )
    shells = {c.name: c.shell for c in (*config.processes, *config.crons)}
    assert shells == {
        "uses-default": "pwsh",
        "own-shell": ["bash", "-lc"],
        "no-shell": "pwsh",  # recorded, but a list command never uses a shell
        "job": "zsh",
    }


def test_no_shell_configured_means_the_system_default(tmp_path):
    [process] = load_config(write(tmp_path, '[[process]]\nname = "a"\ncommand = "x"\n')).processes
    assert process.shell is None


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ('command = "x"\nshell = ""', "'shell' must be"),
        ('command = "x"\nshell = []', "'shell' must be"),
        ('command = "x"\nshell = 3', "'shell' must be"),
        ('command = ["x"]\nshell = "bash"', "only applies to a string 'command'"),
    ],
)
def test_invalid_shell_settings_are_rejected(tmp_path, snippet, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, f'[[process]]\nname = "a"\n{snippet}\n'))


def test_defaults_must_be_a_table(tmp_path):
    with pytest.raises(ConfigError, match=r"'\[defaults\]' must be a table"):
        load_config(write(tmp_path, 'defaults = 1\n[[process]]\nname = "a"\ncommand = "x"\n'))
