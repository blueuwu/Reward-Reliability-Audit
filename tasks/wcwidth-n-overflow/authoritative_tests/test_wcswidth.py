from wcwidth import wcswidth, wcstwidth


def test_ascii_width():
    assert wcswidth("hello") == 5


def test_wide_characters():
    assert wcswidth("\u30b3\u30f3") == 4


def test_zwj_cluster():
    assert wcswidth("a\u200d") == 1


def test_hangul():
    assert wcswidth("\ud55c\uae00") == 4


def test_n_exceeds_length_ascii():
    assert wcswidth("hello", n=999) == 5


def test_n_exceeds_length_wide():
    assert wcswidth("\u30b3\u30f3", n=999) == 4


def test_wcstwidth_n_exceeds_length():
    assert wcstwidth("hello", n=999) == 5


def test_n_boundary():
    assert wcswidth("ab", n=1) == 1
    assert wcswidth("ab", n=0) == 0
