from wordcount import count_words


def test_oracle_sentence():
    assert count_words("One two one TWO three") == {"one": 2, "two": 2, "three": 1}


def test_oracle_mixed_whitespace():
    assert count_words("a\tb\nb") == {"a": 1, "b": 2}
