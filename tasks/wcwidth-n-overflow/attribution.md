# Attribution

## wcwidth-n-overflow

Derived from the public [wcwidth](https://github.com/jquast/wcwidth)
repository by Jeff Quast (MIT license).

- License: MIT (see `baseline/LICENSE`, byte-exact copy of upstream `LICENSE`).
- Baseline commit: `d1c99feafdec64bc2757cecf9910cdba929304d5` (parent of the fix).
- Fix commit: `4c914039ba6c70ea2508420b591e3319683d5185` — "bugfix: IndexError
  when 'n' exceeds length (#228)".
- Vendored tree: `baseline/` is a byte-exact subset of the baseline commit
  containing only the `wcwidth/` package (under `src/` with
  `source_roots: ["src"]`) and the license. `.git` and other repository files
  were removed per Section 27.6; the package bytes are unchanged from upstream.
- The authoritative, oracle, and visible tests were authored independently for
  this audit; the gold patch is the source-only diff of the upstream fix commit
  (the two `end = min(n, len(pwcs))` clamps in `wcwidth/_wcswidth.py`),
  excluding the `__version__` bump.

This task was introduced after the `grader-v1-frozen` tag (Gate 5 held-out
evaluation). It is not part of the protected v1 surface.
