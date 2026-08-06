from timeparse import timeparse


def test_parse_colon_minutes_seconds():
    assert timeparse("1:24") == 84


def test_parse_bare_seconds():
    assert timeparse(":22") == 22


def test_parse_clock_minutes_granularity():
    assert timeparse("1:30", granularity="minutes") == 5400


def test_malformed_returns_none():
    assert timeparse(". day") is None
    assert timeparse("1.2.3 minutes") is None


def test_bare_seconds_minutes_granularity():
    assert timeparse(":22", granularity="minutes") == 22
