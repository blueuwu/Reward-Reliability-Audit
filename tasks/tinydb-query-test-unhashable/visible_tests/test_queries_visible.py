from tinydb.queries import Query


def test_hash_unhashable_list_arg_does_not_raise():
    def in_allowed(value, allowed):
        return value in allowed

    q = Query().val.test(in_allowed, [1, 2, 3])
    assert q({"val": 1})
    assert not q({"val": 9})
    assert hash(q)
