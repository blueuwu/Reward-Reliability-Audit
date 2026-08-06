import pytest

pytest.importorskip("stringutil.optional")

from stringutil.optional import underline


def test_underline_basic():
    assert underline("hi") == "hi\n=="


def test_underline_empty():
    assert underline("") == "\n"
