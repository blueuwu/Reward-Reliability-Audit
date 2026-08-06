import schedule


def test_repr_partial_job_does_not_crash():
    assert repr(schedule.every(10)) == "Every 10 None do [None] (last run: [never], next run: [never])"


def test_repr_complete_job_still_works():
    job = schedule.every().day.at("10:30").do(lambda: 1)
    assert repr(job).startswith("Every 1 day at 10:30:00 do")
