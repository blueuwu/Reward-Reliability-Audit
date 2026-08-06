# Do not crash when converting a partially constructed schedule job to a string

The `schedule` library builds `Job` objects incrementally. A job is
"partially constructed" until a function is attached with `.do(...)`:

    job = schedule.every(10)          # no function attached yet

There is a bug: calling `repr(job)` (or `str(job)`) on a partially constructed
job raises `AttributeError: 'NoneType' object has no attribute 'args'`.

The intended behavior:

- `repr(schedule.every(10))` returns
  `"Every 10 None do [None] (last run: [never], next run: [never])"`
- `repr(schedule.every(5).seconds)` returns
  `"Every 5 seconds do [None] (last run: [never], next run: [never])"`
- A fully constructed job such as
  `schedule.every().day.at("10:30").do(lambda: 1)` still produces a normal repr
  like `"Every 1 day at 10:30:00 do <lambda>() ..."`.

Fix `schedule/__init__.py` so `repr` and `str` never crash on a partially
constructed job, and fully constructed jobs are unaffected.

Tests are in `tests/`. Run them with `python -m pytest tests -q`.
