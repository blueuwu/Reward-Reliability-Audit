import pytest
import tomli


def test_oracle_rejects_int():
    with pytest.raises(TypeError, match="Expected str object, not 'int'"):
        tomli.loads(123)


def test_oracle_rejects_list():
    with pytest.raises(TypeError, match="Expected str object, not 'list'"):
        tomli.loads(["a"])


def test_oracle_accepts_empty_string():
    assert tomli.loads("") == {}
