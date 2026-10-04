import io
import re

import pytest
from rich.console import Console

from devp.hostlog import HostLog, _span
from devp.process import ProcessState


class _Stream(io.StringIO):
    """A text stream that reports the encoding a real terminal or redirect would."""

    def __init__(self, encoding: str) -> None:
        super().__init__()
        self._reported = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._reported


def make_log(width=80, encoding="utf-8"):
    file = _Stream(encoding)
    console = Console(file=file, width=width, force_terminal=False, highlight=False)
    return HostLog(console), file


def lines(file) -> list[str]:
    return [line.rstrip() for line in file.getvalue().splitlines() if line.strip()]


def test_banner_shows_the_url_config_and_api():
    log, file = make_log()
    log.banner("http://localhost:8000/?token=abc", "devp.toml", 3, 5123)
    text = file.getvalue()
    assert "http://localhost:8000/?token=abc" in text
    assert "devp.toml" in text and "3 processes" in text
    assert "127.0.0.1:5123" in text
    assert "Ctrl+C" in text


def test_banner_uses_the_singular_for_one_process():
    log, file = make_log()
    log.banner("http://x", "devp.toml", 1, 1)
    assert "1 process" in file.getvalue() and "1 processes" not in file.getvalue()


def test_event_lines_start_with_a_time_then_glyph_and_label():
    log, file = make_log()
    log.process_state("api", ProcessState.RUNNING)
    (line,) = lines(file)
    assert re.match(r"^\s+\d\d:\d\d:\d\d\s+●\s+api\s+running$", line)


@pytest.mark.parametrize(
    "state, word",
    [
        (ProcessState.STARTING, "starting"),
        (ProcessState.RUNNING, "running"),
        (ProcessState.STOPPED, "stopped"),
        (ProcessState.SCHEDULED, "scheduled"),
    ],
)
def test_process_states_are_logged(state, word):
    log, file = make_log()
    log.process_state("job", state)
    assert word in file.getvalue()


@pytest.mark.parametrize("state", [ProcessState.STOPPING, ProcessState.CRASHED])
def test_noisy_states_are_left_out(state):
    log, file = make_log()
    log.process_state("job", state)
    assert file.getvalue() == ""  # a crash is reported through process_error instead


def test_process_errors_are_logged():
    log, file = make_log()
    log.process_error("worker", "exited with code 3")
    assert "worker" in file.getvalue() and "exited with code 3" in file.getvalue()


def test_square_brackets_in_messages_are_not_read_as_markup():
    log, file = make_log()
    log.process_error("worker", "failed [bold]to start[/bold]")
    assert "[bold]to start[/bold]" in file.getvalue()


def test_sessions_log_join_and_leave_with_duration():
    log, file = make_log()
    log.session("127.0.0.1", joined=True)
    log.session("127.0.0.1", joined=False, seconds=75)
    text = file.getvalue()
    assert "connected" in text and "left after 1m15s" in text


def test_blocked_requests_are_logged_with_the_peer():
    log, file = make_log()
    log.blocked("10.0.0.7")
    assert "10.0.0.7" in file.getvalue() and "token" in file.getvalue()


def test_reload_phases_are_logged_in_order():
    log, file = make_log()
    log.reload_started("devp.toml", 2)
    log.reload_finished(3)
    started, finished = lines(file)
    assert "devp.toml changed" in started and "stopping 2 processes" in started
    assert "reloaded" in finished and "starting 3 processes" in finished


def test_config_errors_keep_their_detail_under_the_message():
    log, file = make_log()
    log.config_error("devp.toml", "could not parse devp.toml: bad\nline two")
    first, *rest = lines(file)
    assert "has an error" in first and "keeping the previous config" in first
    assert "could not parse devp.toml: bad" in rest[0] and "line two" in rest[1]


def test_long_messages_wrap_under_the_message_column():
    log, file = make_log(width=60)
    log.warning("word " * 30)
    first, *wrapped = lines(file)
    assert wrapped, "a long warning should wrap"
    indent = len(first) - len(first.split("word", 1)[1]) - len("word")
    assert all(line.startswith(" " * indent) for line in wrapped)


def test_without_unicode_support_it_falls_back_to_ascii():
    log, file = make_log(encoding="cp437")  # no ● ↻ ✗ in this code page
    log.process_state("api", ProcessState.RUNNING)
    log.reload_started("devp.toml", 1)
    log.banner("http://x", "devp.toml", 1, 1)
    text = file.getvalue()
    assert text.isascii()
    assert "api" in text and "devp.toml changed" in text


@pytest.mark.parametrize(
    "seconds, expected", [(0, "0s"), (59, "59s"), (60, "1m00s"), (3599, "59m59s"), (3660, "1h01m")]
)
def test_span_formatting(seconds, expected):
    assert _span(seconds) == expected
