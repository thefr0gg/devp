import pytest

from devp.config import ConfigError, load_config


def write_config(tmp_path, content):
    path = tmp_path / "devp.toml"
    path.write_text(content)
    return path


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="no config file found"):
        load_config(tmp_path / "devp.toml")


def test_no_entries(tmp_path):
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
    config = load_config(path)
    assert len(config.processes) == 1
    assert config.crons == []
    process = config.processes[0]
    assert process.name == "api"
    assert process.command == "echo hi"
    assert process.autostart is True
    assert process.cwd is None
    assert process.env == {}


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
    process = load_config(path).processes[0]
    assert process.command == ["python", "worker.py"]
    assert process.cwd == "backend"
    assert process.autostart is False
    assert process.env == {"DEBUG": "1"}


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


def test_minimal_cron_job(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[cron]]
        name = "backup"
        command = "python backup.py"
        schedule = "0 * * * *"
        """,
    )
    config = load_config(path)
    assert config.processes == []
    assert len(config.crons) == 1
    cron = config.crons[0]
    assert cron.name == "backup"
    assert cron.command == "python backup.py"
    assert cron.schedule == "0 * * * *"
    assert cron.enabled is True
    assert cron.cwd is None
    assert cron.env == {}


def test_cron_optional_fields(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[cron]]
        name = "backup"
        command = ["python", "backup.py"]
        schedule = "0 0 * * *"
        cwd = "scripts"
        enabled = false
        env = { ENV = "prod" }
        """,
    )
    cron = load_config(path).crons[0]
    assert cron.command == ["python", "backup.py"]
    assert cron.cwd == "scripts"
    assert cron.enabled is False
    assert cron.env == {"ENV": "prod"}


def test_cron_missing_schedule_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[cron]]
        name = "backup"
        command = "python backup.py"
        """,
    )
    with pytest.raises(ConfigError, match="schedule"):
        load_config(path)


def test_cron_invalid_schedule_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[cron]]
        name = "backup"
        command = "python backup.py"
        schedule = "not a schedule"
        """,
    )
    with pytest.raises(ConfigError, match="not a valid cron schedule"):
        load_config(path)


def test_process_and_cron_can_coexist(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        command = "echo hi"

        [[cron]]
        name = "backup"
        command = "python backup.py"
        schedule = "0 * * * *"
        """,
    )
    config = load_config(path)
    assert len(config.processes) == 1
    assert len(config.crons) == 1


def test_autorestart_defaults_to_false(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        command = "echo hi"
        """,
    )
    assert load_config(path).processes[0].autorestart is False


def test_autorestart_can_be_enabled(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        command = "echo hi"
        autorestart = true
        """,
    )
    assert load_config(path).processes[0].autorestart is True


def test_depends_on_valid_order_accepted(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "db"
        command = "echo hi"

        [[process]]
        name = "api"
        command = "echo hi"
        depends_on = ["db"]
        """,
    )
    config = load_config(path)
    assert config.processes[1].depends_on == ["db"]


def test_depends_on_can_reference_a_cron_job(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[cron]]
        name = "warmup"
        command = "echo hi"
        schedule = "0 * * * *"

        [[process]]
        name = "api"
        command = "echo hi"
        depends_on = ["warmup"]
        """,
    )
    config = load_config(path)
    assert config.processes[0].depends_on == ["warmup"]


def test_depends_on_unknown_entry_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        command = "echo hi"
        depends_on = ["ghost"]
        """,
    )
    with pytest.raises(ConfigError, match="unknown entry 'ghost'"):
        load_config(path)


def test_depends_on_self_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        command = "echo hi"
        depends_on = ["api"]
        """,
    )
    with pytest.raises(ConfigError, match="cannot declare depends_on on itself"):
        load_config(path)


def test_depends_on_non_autostart_dependency_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "db"
        command = "echo hi"
        autostart = false

        [[process]]
        name = "api"
        command = "echo hi"
        depends_on = ["db"]
        """,
    )
    with pytest.raises(ConfigError, match="would never start"):
        load_config(path)


def test_depends_on_cycle_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "a"
        command = "echo hi"
        depends_on = ["b"]

        [[process]]
        name = "b"
        command = "echo hi"
        depends_on = ["a"]
        """,
    )
    with pytest.raises(ConfigError, match="circular depends_on"):
        load_config(path)


def test_duplicate_name_across_process_and_cron_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "job"
        command = "echo hi"

        [[cron]]
        name = "job"
        command = "echo hi"
        schedule = "0 * * * *"
        """,
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(path)


def test_ready_checks_parse(tmp_path):
    path = write_config(
        tmp_path,
        """
        [[process]]
        name = "api"
        command = "run-api"
        ready_when = "listening on :\\\\d+"
        ready_timeout = 10

        [[process]]
        name = "db"
        command = "run-db"
        ready_port = 5432
        """,
    )
    api, db = load_config(path).processes
    assert api.ready_when == r"listening on :\d+"
    assert api.ready_timeout == 10.0
    assert db.ready_port == 5432 and db.ready_timeout == 60.0
    assert api.has_ready_check and db.has_ready_check


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ('ready_when = "x"\nready_port = 80', "not both"),
        ('ready_when = "("', "not a valid regular expression"),
        ("ready_port = 70000", "port number"),
        ('ready_port = "80"', "port number"),
        ("ready_timeout = 0", "positive number"),
    ],
)
def test_invalid_ready_checks_rejected(tmp_path, snippet, message):
    path = write_config(tmp_path, f'[[process]]\nname = "api"\ncommand = "x"\n{snippet}\n')
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_watch_patterns_parse_and_validate(tmp_path):
    path = write_config(
        tmp_path, '[[process]]\nname = "api"\ncommand = "x"\nwatch = ["src/**/*.py", "*.toml"]\n'
    )
    assert load_config(path).processes[0].watch == ["src/**/*.py", "*.toml"]

    path = write_config(tmp_path, '[[process]]\nname = "api"\ncommand = "x"\nwatch = "src"\n')
    with pytest.raises(ConfigError, match="'watch' must be a list"):
        load_config(path)
