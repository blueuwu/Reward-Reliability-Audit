# Fix `Query.test` so extra arguments with unhashable values work

TinyDB's `Query` object supports custom predicates:

```python
from tinydb.queries import Query

def in_allowed(value, allowed):
    return value in allowed

query = Query().val.test(in_allowed, [1, 2, 3])
```

There is a bug: when the extra arguments passed to `.test(...)` are
unhashable (a list, a dict, or a tuple containing either), the resulting
query object raises `TypeError: unhashable type: 'list'` the first time it
is hashed — for example when the query is used with a caching table or
compared.

The intended behavior:

- `Query().val.test(in_allowed, [1, 2, 3])` must match `{"val": 1}` and
  must be hashable.
- The same holds for dict arguments and for tuples that contain lists or
  dicts.
- Two queries built from equal arguments must hash equal; different
  arguments must hash differently.
- If an argument cannot be made hashable at all (for example a custom
  object with `__hash__ = None`), the query must still match documents and
  must simply report itself as not cacheable instead of raising.

Fix `tinydb/queries.py`. Tests are in `tests/`; run them with
`python -m pytest tests -q`.
