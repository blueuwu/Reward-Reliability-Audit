import pytest

from tinydb import TinyDB
from tinydb.middlewares import CachingMiddleware
from tinydb.queries import Query
from tinydb.storages import MemoryStorage


def test_oracle_cached_search_with_list_arg():
    db = TinyDB(storage=CachingMiddleware(MemoryStorage)())
    db.insert_multiple([{"val": i} for i in range(10)])

    def in_allowed(value, allowed):
        return value in allowed

    query = Query().val.test(in_allowed, [2, 5, 8])
    assert hash(query)
    assert sorted(doc["val"] for doc in db.search(query)) == [2, 5, 8]


def test_oracle_deep_nested_dict_arg():
    def nested_ok(value, cfg):
        return cfg["levels"]["deep"] == value

    query = Query().val.test(nested_ok, {"levels": {"deep": 42}})
    assert query({"val": 42})
    assert not query({"val": 41})
    assert hash(query)


def test_oracle_any_all_one_of_still_hashable():
    from tinydb.queries import where

    base = Query().name
    assert hash(base.any([Query().x == 1, Query().y == 2]))
    assert hash(base.all([Query().x == 1, Query().y == 2]))
    assert hash(base.one_of([1, 2, 3]))
    assert hash(where("a")["b"] == 3)


def test_oracle_query_can_be_used_after_hash():
    def bounded(value, lo, hi):
        return lo <= value <= hi

    query = Query().score.test(bounded, 10, 20)
    h = hash(query)
    assert query({"score": 15})
    assert query({"score": 20})
    assert not query({"score": 21})
    assert hash(query) == h


def test_oracle_unfreezable_arg_still_matches():
    class Unhashable:
        __hash__ = None

    query = Query().val.test(lambda value, marker: value == 1, Unhashable())
    assert query({"val": 1})
    assert not query.is_cacheable()
