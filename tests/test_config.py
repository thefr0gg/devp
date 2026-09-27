import pytest

from devp.config import ConfigError, load_config


def write_config(tmp_path, content):
    path = tmp_path / "devp.toml"
    path.write_text(content)
    return path


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="no config file found"):
        load_config(tmp_path / "devp.toml")


def test_no_processes(tmp_path):
    path = write_config(tmp_path, "")
    with pytest.raises(ConfigError, match="at least one"):
        load_config(path)


def test_minimal_string_command(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        command = "echo hi"
        """,
    )
    configs = load_config(path)
    assert len(configs) == 1
    assert configs[0].name == "api"
    assert configs[0].command == "echo hi"
    assert configs[0].autostart is True
    assert configs[0].cwd is None
    assert configs[0].env == {}


def test_list_command_and_optional_fields(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "worker"
        command = ["python", "worker.py"]
        cwd = "backend"
        autostart = false
        env = { DEBUG = "1" }
        """,
    )
    configs = load_config(path)
    assert configs[0].command == ["python", "worker.py"]
    assert configs[0].cwd == "backend"
    assert configs[0].autostart is False
    assert configs[0].env == {"DEBUG": "1"}


def test_duplicate_name_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        command = "echo hi"

        [[process]]
        name = "api"
        command = "echo bye"
        """,
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(path)


def test_missing_command_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        """,
    )
    with pytest.raises(ConfigError, match="command"):
        load_config(path)


def test_missing_name_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        command = "echo hi"
        """,
    )
    with pytest.raises(ConfigError, match="name"):
        load_config(path)
