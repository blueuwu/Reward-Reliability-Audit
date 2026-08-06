# Fixture Stringutil

Normalize all whitespace runs in a string to a single ASCII space and strip
leading/trailing whitespace.

The `stringutil.normalize_whitespace(text)` function is implemented in
`src/stringutil/__init__.py`. Today it only collapses runs of ordinary spaces,
so tabs, newlines, and other Unicode whitespace are left untouched. Make
`normalize_whitespace` treat every Unicode whitespace character as a word
separator and strip surrounding whitespace.

Requirements:

- `normalize_whitespace("a\tb") == "a b"`
- `normalize_whitespace("a\n\nb") == "a b"`
- `normalize_whitespace("  spaced   out  ") == "spaced out"`
- `normalize_whitespace("") == ""`

You may edit any file under `src/` and `tests/`. Do not modify `task.yaml`.
