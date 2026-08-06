from inflection import titleize


def test_titleize_non_ascii_second_word():
    assert titleize("ana índia") == "Ana Índia"


def test_titleize_ascii_sentence():
    assert titleize("david's code") == "David's Code"


def test_titleize_underscore():
    assert titleize("some_title") == "Some Title"
