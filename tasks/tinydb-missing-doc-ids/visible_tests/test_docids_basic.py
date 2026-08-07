from tinydb import TinyDB
from tinydb.storages import MemoryStorage


def _db():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple({"int": i} for i in (1, 2, 3))
    return db


def test_get_missing_doc_id_silent():
    db = _db()
    assert db.get(doc_id=99) is None
    assert db.get(doc_ids=[99]) == []


def test_update_existing_doc_id_works():
    db = _db()
    assert sorted(db.update({"int": 9}, doc_ids=[2])) == [2]
    assert db.get(doc_id=2) == {"int": 9}


def test_remove_existing_doc_id_works():
    db = _db()
    assert sorted(db.remove(doc_ids=[2])) == [2]
    assert len(db) == 2
