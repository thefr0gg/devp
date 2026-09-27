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


def test_init_records_the_devp_and_config_versions(tmp_path, monkeypatch):
    from devp.config import CONFIG_VERSION

    monkeypatch.chdir(tmp_path)
    main(["init"])
    config = load_config(tmp_path / "devp.toml")
    assert config.devp_version == __version__
    assert config.config_version == CONFIG_VERSION
    assert config.warnings == []


def test_upgrade_config_stamps_an_unversioned_file_and_keeps_its_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = "# my processes\n\n" + MINIMAL + "\n# trailing note\n"
    (tmp_path / "devp.toml").write_text(original)
    assert load_config(tmp_path / "devp.toml").warnings  # unversioned: warns

    main(["upgrade-config"])

    text = (tmp_path / "devp.toml").read_text()
    assert text.startswith("# my processes\n")
    assert MINIMAL in text and "# trailing note" in text
    assert load_config(tmp_path / "devp.toml").warnings == []


def test_upgrade_config_updates_an_existing_header_in_place(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("devp.config.CONFIG_VERSION", "1.1")
    monkeypatch.setattr("devp.__main__.CONFIG_VERSION", "1.1")
    (tmp_path / "devp.toml").write_text(
        '[devp]\nversion = "0.0.1"  # keep this comment\nconfig-version = "1.0"\n\n' + MINIMAL
    )
    main(["upgrade-config"])
    text = (tmp_path / "devp.toml").read_text()
    assert f'version = "{__version__}"  # keep this comment' in text
    assert 'config-version = "1.1"' in text
    assert load_config(tmp_path / "devp.toml").warnings == []


def test_upgrade_config_refuses_a_different_major_layout(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    original = '[devp]\nconfig-version = "9.0"\n\n' + MINIMAL
    (tmp_path / "devp.toml").write_text(original)
    with pytest.raises(SystemExit):
        main(["upgrade-config"])
    assert (tmp_path / "devp.toml").read_text() == original
    err = " ".join(capsys.readouterr().err.replace("│", " ").split())  # unwrap the box
    assert "can't read" in err


def test_stamping_never_moves_top_level_keys_into_the_devp_table():
    import tomllib

    from devp.__main__ import stamp_versions

    data = tomllib.loads(stamp_versions("top = 1\n" + MINIMAL))
    assert data["top"] == 1 and "top" not in data["devp"]
