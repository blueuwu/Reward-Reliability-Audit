import pytest
import tomli


def test_loads_rejects_bytes_with_clear_message():
    with pytest.raises(TypeError, match="Expected str object, not 'bytes'"):
        tomli.loads(b"v = 1")


def test_loads_rejects_bool_with_clear_message():
    with pytest.raises(TypeError, match="Expected str object, not 'bool'"):
        tomli.loads(False)


def test_loads_rejects_float_with_clear_message():
    with pytest.raises(TypeError, match="Expected str object, not 'float'"):
        tomli.loads(1.5)


def test_loads_rejects_none_with_clear_message():
    with pytest.raises(TypeError, match="Expected str object, not 'NoneType'"):
        tomli.loads(None)


def test_loads_accepts_valid_str():
    assert tomli.loads("v = 1\nw = 2") == {"v": 1, "w": 2}
