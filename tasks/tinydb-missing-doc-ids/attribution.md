# Attribution: tinydb-missing-doc-ids

- **Repository:** https://github.com/msiemens/tinydb
- **License:** MIT — `baseline/LICENSE` (Copyright (C) 2013 Markus Siemens)
- **Fix commit:** `76d21d26c682e1ca6ca25bd8e81edf9f609ac52f`
  "fix: skip missing doc_ids in Table.update and Table.remove (#616)",
  fixing issue #591.
- **Baseline commit:** `8a2dc204c265c07ce8506a3599a28e720b6dcdd7`
- **Vendored snapshot:** `baseline/` contains the `tinydb` package files of
  the baseline commit unchanged (moved under `src/tinydb/` per the harness
  `source_roots` layout) plus the upstream `LICENSE`.
- **Regression tests:** adapted from the `test_*_ids_missing` /
  `test_*_ids_mixed` / `test_doc_id_missing_consistency` tests added by the
  fix commit; the authoritative and oracle suites are independently
  maintained.
- **Task mined:** 2026-08-07, after `grader-v1-frozen`.
