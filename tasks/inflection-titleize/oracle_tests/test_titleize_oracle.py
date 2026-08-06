from inflection import titleize


def test_oracle_verde_acida():
    assert titleize("verde ácida") == "Verde Ácida"


def test_oracle_el_arbol():
    assert titleize("el árbol") == "El Árbol"


def test_oracle_ascii_regression_guard():
    assert titleize("raiders_of_the_lost_ark") == "Raiders Of The Lost Ark"
