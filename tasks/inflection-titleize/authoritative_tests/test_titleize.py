from inflection import titleize


def test_titleize_capitalizes_non_ascii_second_word():
    assert titleize("ana índia") == "Ana Índia"


def test_titleize_non_ascii_second_word_already_capitalized():
    assert titleize("Ana Índia") == "Ana Índia"


def test_titleize_non_ascii_second_word_accented():
    assert titleize("un éléphant") == "Un Éléphant"


def test_titleize_ascii_sentence_preserved():
    assert titleize("david's code") == "David's Code"


def test_titleize_underscore_preserved():
    assert titleize("some_title_here") == "Some Title Here"
