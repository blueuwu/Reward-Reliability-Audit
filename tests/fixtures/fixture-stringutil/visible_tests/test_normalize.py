from stringutil import normalize_whitespace


def test_normalize_tab():
    assert normalize_whitespace("a\tb") == "a b"


def test_normalize_spaces():
    assert normalize_whitespace("  spaced   out  ") == "spaced out"
