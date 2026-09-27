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
    running = [status_label("api", ProcessState.RUNNING, frame=i) for i in range(16)]
    assert all(running[i] != running[i + 1] for i in range(15))  # it visibly moves
    assert running[:8] == running[8:]  # and loops
    assert len(set(running)) > 3
    assert status_label("api", ProcessState.CRASHED, frame=0) == status_label(
        "api", ProcessState.CRASHED, frame=3
    )


def test_glyphs_are_single_width_text_not_emoji():
    import unicodedata

    # Not emoji, and not East Asian "ambiguous" width (drawn 2 columns wide by some
    # CJK-locale terminals): every glyph must be exactly one column everywhere.
    emoji_capable = set("✨✳✴❇●✕★☆")
    for state in ProcessState:
        for frame in range(20):
            glyph = status_label("x", state, frame=frame).split("]")[1][0]
            assert glyph not in emoji_capable
            assert unicodedata.east_asian_width(glyph) == "N", (state, glyph)
