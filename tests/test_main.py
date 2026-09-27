import pytest

from devp.__main__ import main


def test_main_exits_with_error_when_config_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "devp.toml" in capsys.readouterr().err
