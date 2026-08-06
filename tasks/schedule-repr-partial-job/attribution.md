# Attribution

## schedule-repr-partial-job

Derived from the public [schedule](https://github.com/dbader/schedule)
repository by Daniel Bader and contributors (MIT license).

- License: MIT (see `baseline/LICENSE.txt`).
- Baseline commit: `a3ecd3548aff70bef15a61baee0d2d7f22f57992` (parent of the fix).
- Fix commit: `3863effe15ec8239ba88c65a4231a67e43b116df` — "Do not crash repr on
  partially constructed job" (issue #569).
- Vendored tree: `baseline/` is a byte-exact subset of the baseline commit
  containing only `schedule/__init__.py` and `LICENSE.txt`; `.git`, tests, and
  other repository files were removed per Section 27.6. `schedule/` is vendored
  under `src/` (with `source_roots: ["src"]`) so the package is importable from
  a declared workspace source root; its bytes are unchanged from upstream.
- The authoritative, oracle, and visible tests were authored independently for
  this audit; the gold patch is the source-only diff of the upstream fix commit.
