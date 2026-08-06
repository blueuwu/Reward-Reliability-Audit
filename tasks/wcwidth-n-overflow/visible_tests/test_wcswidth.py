from wcwidth import wcswidth, wcstwidth


def test_ascii_width():
    assert wcswidth("hello") == 5


def test_wide_characters():
    assert wcswidth("\u30b3\u30f3") == 4


def test_n_exceeds_length():
    assert wcswidth("hello", n=999) == 5


def test_wcstwidth_n_exceeds_length():
    assert wcstwidth("hello", n=999) == 5
