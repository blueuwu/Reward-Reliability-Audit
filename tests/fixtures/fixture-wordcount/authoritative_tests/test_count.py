from wordcount import count_words


def test_mixed_case():
    assert count_words("The the THE") == {"the": 3}


def test_empty():
    assert count_words("") == {}


def test_single():
    assert count_words("Hello") == {"hello": 1}


def test_repeated():
    assert count_words("a A a A a") == {"a": 5}
