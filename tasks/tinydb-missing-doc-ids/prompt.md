# Fix `Table.update` and `Table.remove` for missing document IDs

TinyDB's `Table` supports operating on documents by their document ID:

```python
from tinydb import TinyDB
from tinydb.storages import MemoryStorage

db = TinyDB(storage=MemoryStorage)
db.insert({"int": 1})
```

There is a bug: `Table.get(doc_id=N)` and `Table.get(doc_ids=[N])` return
silently when document `N` does not exist, but
`Table.update(fields, doc_ids=[N])` and `Table.remove(doc_ids=[N])` raise
`KeyError`. Worse, when a mix of existing and missing IDs is passed to
`update`, existing documents are mutated in memory before the `KeyError` is
raised, leaving the table partially updated.

The intended behavior (matching `get`):

- `update(fields, doc_ids=[N])` and `remove(doc_ids=[N])` must not raise
  when `N` does not exist; the returned list must only contain the IDs
  that were actually updated or removed.
- With a mix of existing and missing IDs, the existing documents must be
  updated/removed and the missing IDs silently skipped — atomically, with
  no partial state on failure.
- `upsert` with a missing `doc_id` must fall through and insert the
  document.
- `update`/`remove` with an empty list or with a generator of IDs must
  behave the same as with a list.

Fix `tinydb/table.py`. Tests are in `tests/`; run them with
`python -m pytest tests -q`.
