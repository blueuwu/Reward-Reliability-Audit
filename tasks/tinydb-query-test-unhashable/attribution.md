# Attribution: tinydb-query-test-unhashable

- **Repository:** https://github.com/msiemens/tinydb
- **License:** MIT — `baseline/LICENSE` (Copyright (C) 2013 Markus Siemens)
- **Fix commit:** `770486ff8217eb50c8fecb6ce9f54c140c8225c5`
  "fix: freeze unhashable args in Query.test to prevent TypeError (#618)",
  fixing issue #517.
- **Baseline commit:** `e70f9b1d91b6145b5083042eebe266ef0e4ccd27`
- **Vendored snapshot:** `baseline/` contains the `tinydb` package files of
  the baseline commit unchanged (moved under `src/tinydb/` per the harness
  `source_roots` layout) plus the upstream `LICENSE`.
- **Regression tests:** adapted from
  `tests/test_queries.py::test_custom_with_unhashable_params` added by the
  fix commit; the authoritative and oracle suites are independently
  maintained.
- **Task mined:** 2026-08-07, after `grader-v1-frozen`.
