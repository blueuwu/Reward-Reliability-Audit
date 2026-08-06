from stringutil import normalize_whitespace


def test_normalize_tab():
    assert normalize_whitespace("a\tb") == "a b"


def test_normalize_newline():
    assert normalize_whitespace("a\n\nb") == "a b"


def test_normalize_spaces():
    assert normalize_whitespace("  spaced   out  ") == "spaced out"


def test_normalize_empty():
    assert normalize_whitespace("") == ""


def test_normalize_single():
    assert normalize_whitespace("x") == "x"


def test_normalize_nbsp():
    assert normalize_whitespace("a\u00a0b") == "a b"
