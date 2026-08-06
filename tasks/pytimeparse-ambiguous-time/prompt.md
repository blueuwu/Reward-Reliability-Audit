# Fix timeparse() crash on ambiguous and malformed inputs

The vendored `src/timeparse.py` provides `timeparse(sval, granularity="seconds")`,
which parses a human time expression into a number of seconds (an `int` when
possible, otherwise a `float`). Per its contract it must return `None` when the
string cannot be parsed.

Two behaviors are currently broken:

1. `timeparse()` raises an uncaught `KeyError` when the
   `granularity="minutes"` option is used with a bare-seconds clock expression
   such as `":22"`. It must instead interpret the digits as minutes and return
   a number of seconds.
2. `timeparse()` raises an uncaught `ValueError` when a numeric field is
   malformed (for example a lone `.` or a value containing more than one
   decimal point). It must instead treat the expression as unparseable and
   return `None`.

Requirements:

- Keep the public signature `timeparse(sval, granularity="seconds")`.
- Ambiguous `MM:SS` clock expressions must follow `granularity`: with the
  default `"seconds"`, `"1:30"` is 90 seconds; with `"minutes"`, `"1:30"` is
  5400 seconds.
- Malformed or unparseable expressions must return `None`, never raise.
- Normal expressions (`"1:24"`, `":22"`, `"1 minute, 24 secs"`, `"1.2
  minutes"`, `"1.2 seconds"`, signed values, hours/days/weeks) must keep
  returning the same results as before.

Make the smallest change to `src/timeparse.py` that satisfies these
requirements.
