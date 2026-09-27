from devp.widgets import format_duration


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
