from tinydb import TinyDB, where
from tinydb.storages import JSONStorage, MemoryStorage
from tinydb.table import Document


def test_oracle_upsert_with_missing_doc_id_inserts(tmp_path):
    db = TinyDB(tmp_path / "db.json", storage=JSONStorage)
    db.insert({"name": "a"})
    db.upsert(Document({"name": "new", "k": 1}, doc_id=99))
    assert db.get(doc_id=99) == {"name": "new", "k": 1}
    assert len(db) == 2


def test_oracle_upsert_with_existing_doc_id_updates(tmp_path):
    db = TinyDB(tmp_path / "db.json", storage=JSONStorage)
    db.insert({"name": "a"})
    db.upsert(Document({"name": "a", "k": 1}, doc_id=1))
    assert db.get(doc_id=1) == {"name": "a", "k": 1}
    assert len(db) == 1


def test_oracle_atomicity_survives_reload(tmp_path):
    db = TinyDB(tmp_path / "db.json", storage=JSONStorage)
    db.insert_multiple({"int": i * 10} for i in (1, 2, 3))
    db.update({"int": 9}, doc_ids=[1, 99])
    db2 = TinyDB(tmp_path / "db.json", storage=JSONStorage)
    assert db2.count(where("int") == 10) == 0
    assert db2.count(where("int") == 9) == 1


def test_oracle_update_with_empty_and_generator_inputs():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple({"int": i * 10} for i in (1, 2, 3))
    assert db.update({"int": 9}, doc_ids=[]) == []
    assert len(db) == 3
    assert sorted(db.update({"int": 9}, doc_ids=(i for i in (2, 99)))) == [2]
    assert db.count(where("int") == 9) == 1
    assert sorted(db.remove(doc_ids=(i for i in (1, 99)))) == [1]
    assert len(db) == 2


def test_oracle_duplicate_doc_ids_update():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple({"int": i * 10} for i in (1, 2, 3))
    assert sorted(db.update({"int": 9}, doc_ids=[1, 1])) == [1, 1]
    assert db.count(where("int") == 9) == 1
    assert len(db) == 3


def test_oracle_cond_and_doc_ids_still_work():
    db = TinyDB(storage=MemoryStorage)
    db.insert_multiple({"int": i * 10} for i in (1, 2, 3))
    assert sorted(db.update({"int": 9}, where("int") == 20)) == [2]
    assert db.count(where("int") == 9) == 1
    assert sorted(db.remove(where("int") == 10)) == [1]
    assert len(db) == 2
