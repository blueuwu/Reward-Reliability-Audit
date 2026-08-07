import pytest

from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


@pytest.fixture
def db():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple({"int": i * 10} for i in (1, 2, 3))
    return db


def test_authoritative_update_ids_missing(db):
    assert db.update({"int": 9}, doc_ids=[99]) == []
    assert db.count(where("int") == 9) == 0
    assert db.count(where("int") == 10) == 1


def test_authoritative_update_ids_mixed_atomic(db):
    assert sorted(db.update({"int": 9}, doc_ids=[1, 99])) == [1]
    assert db.count(where("int") == 9) == 1
    assert db.count(where("int") == 10) == 0


def test_authoritative_remove_ids_missing(db):
    assert db.remove(doc_ids=[99]) == []
    assert len(db) == 3


def test_authoritative_remove_ids_mixed_atomic(db):
    assert sorted(db.remove(doc_ids=[1, 99])) == [1]
    assert len(db) == 2
    assert db.get(doc_id=1) is None
    assert db.get(doc_id=2) is not None


def test_authoritative_get_update_remove_consistency(db):
    assert db.get(doc_id=99) is None
    assert db.get(doc_ids=[99]) == []
    assert db.update({"int": 9}, doc_ids=[99]) == []
    assert db.remove(doc_ids=[99]) == []
    assert len(db) == 3
