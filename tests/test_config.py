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


PROCESS = '[[process]]\nname = "api"\ncommand = "x"\n'


def with_versions(config_version=None, devp_version=None) -> str:
    lines = ["[devp]"]
    if devp_version is not None:
        lines.append(f'version = "{devp_version}"')
    if config_version is not None:
        lines.append(f'config-version = "{config_version}"')
    return "\n".join(lines) + "\n\n" + PROCESS


def test_matching_versions_load_without_warnings(tmp_path):
    from devp import __version__
    from devp.config import CONFIG_VERSION

    config = load_config(write_config(tmp_path, with_versions(CONFIG_VERSION, __version__)))
    assert config.warnings == []
    assert config.config_version == CONFIG_VERSION and config.devp_version == __version__


def test_missing_config_version_warns(tmp_path):
    config = load_config(write_config(tmp_path, PROCESS))
    assert len(config.warnings) == 1
    assert "doesn't record a config-version" in config.warnings[0]
    assert "devp upgrade-config" in config.warnings[0]


def test_older_minor_layout_warns(tmp_path, monkeypatch):
    monkeypatch.setattr("devp.config.CONFIG_VERSION", "1.2")
    [warning] = load_config(write_config(tmp_path, with_versions("1.0"))).warnings
    assert "uses config layout 1.0; this devp uses 1.2" in warning


def test_newer_minor_layout_warns(tmp_path, monkeypatch):
    monkeypatch.setattr("devp.config.CONFIG_VERSION", "1.0")
    [warning] = load_config(write_config(tmp_path, with_versions("1.3"))).warnings
    assert "newer than this devp understands" in warning


@pytest.mark.parametrize(("found", "advice"), [("2.0", "Update devp"), ("0.9", "1.x layout")])
def test_different_major_layout_is_refused(tmp_path, monkeypatch, found, advice):
    monkeypatch.setattr("devp.config.CONFIG_VERSION", "1.0")
    with pytest.raises(ConfigError, match=f"layout {found}.*can't read.*{advice}"):
        load_config(write_config(tmp_path, with_versions(found)))


def test_config_generated_by_a_newer_devp_warns(tmp_path):
    from devp.config import CONFIG_VERSION

    [warning] = load_config(write_config(tmp_path, with_versions(CONFIG_VERSION, "99.0.0"))).warnings
    assert "generated by devp 99.0.0, newer than this one" in warning


@pytest.mark.parametrize("bad", ["1", "1.0.0", "one.two"])
def test_malformed_config_version_is_rejected(tmp_path, bad):
    with pytest.raises(ConfigError, match="config-version must look like"):
        load_config(write_config(tmp_path, with_versions(bad)))


def test_version_metadata_does_not_affect_config_equality(tmp_path):
    from devp.config import CONFIG_VERSION

    plain = load_config(write_config(tmp_path, PROCESS))
    stamped = load_config(write_config(tmp_path, with_versions(CONFIG_VERSION, "0.1.0")))
    assert plain == stamped


def test_venv_is_parsed_for_processes_and_crons(tmp_path):
    path = tmp_path / "devp.toml"
    path.write_text(
        '[[process]]\nname = "a"\ncommand = "x"\nvenv = ".venv"\n'
        '[[cron]]\nname = "b"\ncommand = "x"\nschedule = "* * * * *"\nvenv = "v"\n'
    )
    config = load_config(path)
    assert config.processes[0].venv == ".venv"
    assert config.crons[0].venv == "v"


def test_venv_must_be_a_non_empty_string(tmp_path):
    path = tmp_path / "devp.toml"
    path.write_text('[[process]]\nname = "a"\ncommand = "x"\nvenv = 3\n')
    with pytest.raises(ConfigError, match="'venv' must be a non-empty string"):
        load_config(path)


@pytest.mark.parametrize(
    "env_line",
    ['env = "DEBUG=1"', "env = { DEBUG = 1 }", "env = { DEBUG = true }", 'env = ["A=1"]'],
)
def test_process_env_must_be_a_table_of_strings(tmp_path, env_line):
    path = tmp_path / "devp.toml"
    path.write_text(f'[[process]]\nname = "a"\ncommand = "x"\n{env_line}\n')
    with pytest.raises(ConfigError, match="'env' must be a table of string to string"):
        load_config(path)


def test_cron_env_must_be_a_table_of_strings(tmp_path):
    path = tmp_path / "devp.toml"
    path.write_text(
        '[[cron]]\nname = "a"\ncommand = "x"\nschedule = "* * * * *"\nenv = { N = 1 }\n'
    )
    with pytest.raises(ConfigError, match="'env' must be a table of string to string"):
        load_config(path)


def test_env_is_per_entry_and_not_shared(tmp_path):
    path = tmp_path / "devp.toml"
    path.write_text(
        '[[process]]\nname = "a"\ncommand = "x"\nenv = { A = "1" }\n'
        '[[process]]\nname = "b"\ncommand = "x"\n'
        '[[cron]]\nname = "c"\ncommand = "x"\nschedule = "* * * * *"\nenv = { C = "3" }\n'
    )
    config = load_config(path)
    assert config.processes[0].env == {"A": "1"}
    assert config.processes[1].env == {}
    assert config.crons[0].env == {"C": "3"}
