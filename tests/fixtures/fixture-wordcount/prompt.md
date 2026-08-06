# Fixture Wordcount

Count word frequencies in a string case-insensitively.

The `wordcount.count_words(text)` function is implemented in
`src/wordcount/__init__.py`. Today it counts words case-sensitively, so
`count_words("The the THE")` returns separate entries for each casing. Make the
count case-insensitive.

Requirements:

- `count_words("The the THE") == {"the": 3}`
- `count_words("Hello") == {"hello": 1}`
- `count_words("") == {}`

You may edit any file under `src/` and `tests/`. Do not modify `task.yaml`.
