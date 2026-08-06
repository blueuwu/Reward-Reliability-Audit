# Fix `titleize` for words starting with non-ASCII letters

The `inflection` library provides `titleize(word)`, meant to "capitalize all the
words" in a string for pretty output.

There is a bug: `titleize` only capitalizes words that start with ASCII letters
(`A`-`Z`). Words that start with accented or non-ASCII letters are left
lowercase unless they happen to be the very first word of the string.

Examples of the intended behavior:

- `titleize("ana índia")` should return `"Ana Índia"`
- `titleize("un éléphant")` should return `"Un Éléphant"`
- `titleize("david's code")` should return `"David's Code"`
- `titleize("some_title_here")` should return `"Some Title Here"`

The first two examples currently produce the wrong output. Fix `inflection.py`
so every word is capitalized correctly, including words that start with
non-ASCII letters, without breaking the existing ASCII cases.

Tests are in `tests/`. Run them with `python -m pytest tests -q`.
