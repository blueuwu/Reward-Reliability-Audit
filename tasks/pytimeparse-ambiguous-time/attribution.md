# Attribution

## pytimeparse-ambiguous-time

Derived from the public [pytimeparse](https://github.com/wroberts/pytimeparse)
repository by Will Roberts (MIT license).

- License: MIT (see `baseline/LICENSE`, byte-exact copy of upstream `LICENSE.rst`).
- Baseline commit: `9ad0a82d239613c77f1b899a7d9fc6b07efece7d` (parent of the fix).
- Fix commit: `c341eacbb32db457571b883f481c5d7578718704` — "fix: KeyError in
  `_interpret_as_minutes` + ValueError on malformed floats (fixes #15)".
- Vendored tree: `baseline/` is a byte-exact subset of the baseline commit
  containing only `timeparse.py` (under `src/` with `source_roots: ["src"]`)
  and the license. `.git` and other repository files were removed per Section
  27.6; the `timeparse.py` bytes are unchanged from upstream.
- The authoritative, oracle, and visible tests were authored independently for
  this audit; the gold patch is the source-only diff of the upstream fix commit.

This task was introduced after the `grader-v1-frozen` tag (Gate 5 held-out
evaluation). It is not part of the protected v1 surface.
