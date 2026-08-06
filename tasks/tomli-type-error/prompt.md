# Make `tomli.loads` raise a clear TypeError for non-string input

The `tomli` library is a pure-Python TOML parser. Its public `tomli.loads(s)`
function is documented to accept a `str` and returns a `dict`.

There is a bug: when `loads` is called with a non-string argument such as
`False`, `1.5`, `None`, or a `bytes` object, it raises a confusing error
(`AttributeError: 'bool' object has no attribute 'replace'`, or a misleading
`TypeError`) instead of a clear, consistent `TypeError` explaining that a `str`
is required.

The intended behavior:

- `tomli.loads(b"v = 1")` raises `TypeError` with message
  `Expected str object, not 'bytes'`
- `tomli.loads(False)` raises `TypeError` with message
  `Expected str object, not 'bool'`
- `tomli.loads(1.5)` raises `TypeError` with message
  `Expected str object, not 'float'`
- `tomli.loads("v = 1")` returns `{"v": 1}`

Fix `src/tomli/_parser.py` so every non-string input raises a clear `TypeError`
with an `Expected str object, not '<type>'` message, while valid strings still
parse correctly.

Tests are in `tests/`. Run them with `python -m pytest tests -q`.
