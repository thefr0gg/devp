from devp.process import ProcessState
from devp.widgets import format_duration, is_animated, status_label


def test_format_duration_seconds():
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"
    assert format_duration(59.9) == "59s"


def test_format_duration_minutes():
    assert format_duration(60) == "1m00s"
    assert format_duration(312) == "5m12s"


def test_format_duration_hours():
    assert format_duration(3600) == "1h00m"
    assert format_duration(7500) == "2h05m"


def test_format_duration_days():
    assert format_duration(86400) == "1d00h"
    assert format_duration(90000 + 3600) == "1d02h"


def test_format_duration_clamps_negative():
    assert format_duration(-5) == "0s"


def test_active_states_animate_and_idle_states_do_not():
    assert is_animated(ProcessState.RUNNING)
    assert is_animated(ProcessState.STOPPING)
    assert is_animated(ProcessState.SCHEDULED)
    assert not is_animated(ProcessState.STOPPED)
    assert not is_animated(ProcessState.CRASHED)


def test_status_label_cycles_frames():
    frames = {status_label("api", ProcessState.RUNNING, frame=i) for i in range(10)}
    assert len(frames) == 10
    assert status_label("api", ProcessState.RUNNING, frame=0) == status_label(
        "api", ProcessState.RUNNING, frame=10
    )
    assert status_label("api", ProcessState.CRASHED, frame=0) == status_label(
        "api", ProcessState.CRASHED, frame=3
    )


def test_glyphs_are_not_emoji():
    # Braille patterns and ✗ have no emoji presentation, so they stay one cell wide.
    for state in ProcessState:
        for frame in range(10):
            glyph = status_label("x", state, frame=frame).split("]")[1][0]
            assert glyph == "✗" or 0x2800 <= ord(glyph) <= 0x28FF
