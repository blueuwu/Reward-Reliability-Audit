from stringutil import normalize_whitespace


def test_oracle_em_space():
    assert normalize_whitespace("a\u2003b") == "a b"


def test_oracle_mixed():
    assert normalize_whitespace("\t a \u00a0\n b ") == "a b"
