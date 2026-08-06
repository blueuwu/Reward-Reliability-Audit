from wcwidth import wcswidth, wcstwidth, wcwidth


def test_oracle_n_overflow_wcswidth():
    assert wcswidth("abcde", n=100) == 5


def test_oracle_n_overflow_wcstwidth():
    assert wcstwidth("\u30b3\u30f3", n=100) == 4


def test_oracle_zwj_cluster_width():
    assert wcswidth("a\u200d") == 1


def test_oracle_single_char_width():
    assert wcwidth("a") == 1


def test_oracle_hangul_width():
    assert wcswidth("\ud55c\uae00") == 4
