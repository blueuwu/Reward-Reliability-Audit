# Attribution

## tomli-type-error

Derived from the public [tomli](https://github.com/hukkin/tomli) repository by
Taneli Hukkinen (MIT license).

- License: MIT (see `baseline/LICENSE`).
- Baseline commit: `facdab0f5aacc5eb223753c42604d5de7bdaee9d` (parent of the fix).
- Fix commit: `4e245a4bbbefed99e550e196095ea65c851cf31d` — "`tomli.loads`:
  Raise TypeError not AttributeError. Improve message" (PR #229).
- Vendored tree: `baseline/` is a byte-exact subset of the baseline commit
  containing only `src/tomli/` (the importable package) and `LICENSE`; `.git`,
  tests, and other repository files were removed per Section 27.6.
- The authoritative, oracle, and visible tests were authored independently for
  this audit; the gold patch is the source-only diff of the upstream fix commit.
