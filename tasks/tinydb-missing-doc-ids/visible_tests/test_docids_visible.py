from tinydb import TinyDB
from tinydb.storages import MemoryStorage


def _db():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple({"int": i} for i in (1, 2, 3))
    return db


def test_update_missing_doc_id_does_not_raise():
    db = _db()
    assert db.update({"int": 9}, doc_ids=[99]) == []
    assert len(db) == 3


def test_remove_missing_doc_id_does_not_raise():
    db = _db()
    assert db.remove(doc_ids=[99]) == []
    assert len(db) == 3
