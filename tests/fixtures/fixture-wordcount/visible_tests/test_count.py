from wordcount import count_words


def test_basic_words():
    assert count_words("hello world") == {"hello": 1, "world": 1}


def test_mixed_case():
    assert count_words("The the THE") == {"the": 3}
