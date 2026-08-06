import pytest
import tomli


def test_loads_rejects_bytes():
    with pytest.raises(TypeError, match="Expected str object"):
        tomli.loads(b"v = 1")


def test_loads_rejects_bool():
    with pytest.raises(TypeError, match="Expected str object"):
        tomli.loads(False)


def test_loads_accepts_str():
    assert tomli.loads("v = 1") == {"v": 1}
