import os

import pytest

from devp import __version__
from devp.__main__ import STARTER_CONFIG, find_config, main
from devp.config import load_config

MINIMAL = '[[process]]\nname = "api"\ncommand = "echo hi"\n'


@pytest.fixture
def ran(monkeypatch):
    """Stub out the TUI; record the config it would have run and the directory it ran in."""
    runs = []

    def fake_run(self):
        runs.append((list(self.manager.processes), os.getcwd()))

    monkeypatch.setattr("devp.app.DevpApp.run", fake_run)
    return runs


def test_main_exits_with_error_when_config_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "devp.toml" in err and "devp init" in err


def test_finds_config_in_a_parent_directory_and_runs_from_there(tmp_path, monkeypatch, ran):
    (tmp_path / "devp.toml").write_text(MINIMAL)
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    main([])

    assert ran == [(["api"], str(tmp_path))]


def test_nearest_config_wins(tmp_path):
    (tmp_path / "devp.toml").write_text(MINIMAL)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "devp.toml").write_text(MINIMAL)
    assert find_config(tmp_path / "sub") == tmp_path / "sub" / "devp.toml"


def test_config_flag(tmp_path, monkeypatch, ran):
    config_dir = tmp_path / "project"
    config_dir.mkdir()
    (config_dir / "custom.toml").write_text(MINIMAL)
    monkeypatch.chdir(tmp_path)

    main(["-c", "project/custom.toml"])

    assert ran == [(["api"], str(config_dir))]


def test_config_flag_with_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["--config", "nope.toml"])
    assert "no config file found" in capsys.readouterr().err


def test_init_writes_a_valid_starter_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["init"])

    path = tmp_path / "devp.toml"
    assert path.read_text() == STARTER_CONFIG
    assert "Created devp.toml" in capsys.readouterr().out
    config = load_config(path)
    assert [p.name for p in config.processes] == ["web"]


def test_init_refuses_to_overwrite_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "devp.toml").write_text(MINIMAL)

    with pytest.raises(SystemExit) as exc_info:
        main(["init"])
    assert exc_info.value.code == 1
    assert (tmp_path / "devp.toml").read_text() == MINIMAL
    assert "--force" in capsys.readouterr().err

    main(["init", "--force"])
    assert (tmp_path / "devp.toml").read_text() == STARTER_CONFIG


def test_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"devp {__version__}"
