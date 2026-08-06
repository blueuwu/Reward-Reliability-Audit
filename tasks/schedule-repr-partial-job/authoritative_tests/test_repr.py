import schedule


def test_repr_partial_interval_job():
    assert repr(schedule.every(10)) == "Every 10 None do [None] (last run: [never], next run: [never])"


def test_repr_partial_unit_job_seconds():
    assert repr(schedule.every(5).seconds) == "Every 5 seconds do [None] (last run: [never], next run: [never])"


def test_repr_partial_unit_job_days():
    assert repr(schedule.every().day) == "Every 1 day do [None] (last run: [never], next run: [never])"


def test_repr_complete_job_preserved():
    job = schedule.every().day.at("10:30").do(lambda: 1)
    result = repr(job)
    assert result.startswith("Every 1 day at 10:30:00 do")
    assert "[None]" not in result
