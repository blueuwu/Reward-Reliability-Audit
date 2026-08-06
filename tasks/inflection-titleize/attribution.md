# Attribution

## inflection-titleize

Derived from the public [inflection](https://github.com/jpvanhal/inflection)
repository by Janne Vanhala (MIT license).

- License: MIT (see `baseline/LICENSE`).
- Baseline commit: `1969b3a06a9ff06d023863e388cf8af01978d297` (parent of the fix).
- Fix commit: `e32443bb7dc1ba91a15336b5baab908d25f4b93a` — "Fix titleize()
  capitalizing only words starting A-Z" (issue #33).
- Vendored tree: `baseline/` is a byte-exact subset of the baseline commit
  containing only `inflection.py` and `LICENSE`; `.git` and other repository
  files were removed per Section 27.6. `inflection.py` is vendored under
  `src/` (with `source_roots: ["src"]`) so the module is importable from a
  declared workspace source root; its bytes are unchanged from upstream.
- The authoritative, oracle, and visible tests were authored independently for
  this audit; the gold patch is the source-only diff of the upstream fix commit.
