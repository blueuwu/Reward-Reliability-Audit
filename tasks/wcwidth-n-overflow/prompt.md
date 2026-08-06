# Fix wcswidth()/wcstwidth() crash with the legacy n argument

The vendored `src/wcwidth/` package measures the display width of Unicode
strings for terminals. Two functions accept an optional legacy POSIX `n`
argument that limits the measurement to the first `n` characters:

- `wcswidth(text, n=None, ...)` and
- `wcstwidth(text, n=None, ...)`.

When `n` is larger than the length of the string, both functions raise an
uncaught `IndexError` instead of measuring the whole string.

Requirements:

- `wcswidth(text, n)` and `wcstwidth(text, n)` must behave like measuring the
  whole string when `n >= len(text)`; they must never raise `IndexError`.
- With `n is None` (or `n` within bounds) the existing widths must be
  unchanged: ASCII characters are width 1, wide CJK characters are width 2,
  and combining/zero-width characters (e.g. ZWJ sequences) are width 0/1 as
  before.
- Keep the public functions `wcwidth`, `wcswidth`, and `wcstwidth` working.

Make the smallest change to the package source that satisfies these
requirements.
