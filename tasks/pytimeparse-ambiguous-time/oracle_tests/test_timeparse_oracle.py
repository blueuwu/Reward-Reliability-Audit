from timeparse import timeparse


def test_oracle_malformed_double_dot_hours():
    assert timeparse("1.2.3 hours") is None


def test_oracle_clock_with_seconds():
    assert timeparse("1:30:15") == 5415


def test_oracle_fractional_clock():
    assert timeparse("1:00:00.5") == 3600.5


def test_oracle_minutes_granularity_thirty():
    assert timeparse(":30", granularity="minutes") == 30


def test_oracle_signed_clock():
    assert timeparse("-1:30") == -90


def test_oracle_multiple_units():
    assert timeparse("1 h 30 m") == 5400
