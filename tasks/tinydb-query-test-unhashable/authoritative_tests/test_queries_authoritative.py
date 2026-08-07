from tinydb.queries import Query, where


def test_authoritative_list_arg_hashable():
    def in_allowed(value, allowed):
        return value in allowed

    query = Query().val.test(in_allowed, [1, 2, 3])
    assert query({"val": 1})
    assert not query({"val": 4})
    assert hash(query)


def test_authoritative_dict_arg_hashable():
    def has_key(value, mapping):
        return value in mapping

    query = Query().val.test(has_key, {"a": 1, "b": 2})
    assert query({"val": "a"})
    assert not query({"val": "c"})
    assert hash(query)


def test_authoritative_equal_args_equal_hash():
    def in_allowed(value, allowed):
        return value in allowed

    q_a = Query().val.test(in_allowed, [1, 2, 3])
    q_b = Query().val.test(in_allowed, [1, 2, 3])
    assert hash(q_a) == hash(q_b)
    q_c = Query().val.test(in_allowed, [1, 2, 4])
    assert hash(q_a) != hash(q_c)


def test_authoritative_nested_tuple_arg_frozen():
    def second_item_has_flag(value, pair):
        return pair[1].get("flag") == value

    query = Query().val.test(second_item_has_flag, (1, {"flag": "x"}))
    assert query({"val": "x"})
    assert not query({"val": "y"})
    assert hash(query)


def test_authoritative_unfreezable_arg_uncacheable():
    class Unhashable:
        __hash__ = None

    query = Query().val.test(lambda value, x: True, Unhashable())
    assert query({"val": 1})
    assert not query.is_cacheable()


def test_authoritative_fragment_and_where_still_hashable():
    q = Query().name == "Ada"
    assert hash(q)
    assert hash(where("value").exists())
