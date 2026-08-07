from tinydb.queries import Query, where


def test_plain_equality_query_still_works():
    q = Query().name == "Ada"
    assert q({"name": "Ada"})
    assert not q({"name": "Bob"})
    assert hash(q)


def test_where_path_queries_still_work():
    assert where("value").exists()({"value": 1})
    assert not where("value").exists()({"other": 1})
