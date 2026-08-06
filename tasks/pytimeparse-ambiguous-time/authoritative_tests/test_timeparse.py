from timeparse import timeparse


def test_parse_colon_minutes_seconds():
    assert timeparse("1:24") == 84


def test_parse_bare_seconds():
    assert timeparse(":22") == 22


def test_parse_clock_minutes_granularity():
    assert timeparse("1:30", granularity="minutes") == 5400


def test_parse_bare_seconds_minutes_granularity():
    assert timeparse(":22", granularity="minutes") == 22


def test_parse_unit_words():
    assert timeparse("1 minute, 24 secs") == 84
    assert timeparse("1m24s") == 84


def test_parse_fractional_minutes():
    assert timeparse("1.2 minutes") == 72


def test_parse_fractional_seconds():
    assert timeparse("1.2 seconds") == 1.2


def test_parse_signed():
    assert timeparse("- 1 minute") == -60
    assert timeparse("+ 1 minute") == 60


def test_parse_hours_and_days():
    assert timeparse("2 hours") == 7200
    assert timeparse("1 day") == 86400
    assert timeparse("1 week") == 604800


def test_malformed_dot_day_returns_none():
    assert timeparse(". day") is None


def test_malformed_double_dot_returns_none():
    assert timeparse("1.2.3 minutes") is None


def test_unparseable_returns_none():
    assert timeparse("") is None
    assert timeparse("not a time") is None
