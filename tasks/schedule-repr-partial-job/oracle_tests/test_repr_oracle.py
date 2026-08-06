import schedule


def test_oracle_repr_partial_week():
    assert "[None]" in repr(schedule.every().week)


def test_oracle_repr_named_function():
    def greet() -> str:
        return "hi"

    job = schedule.every().minute.do(greet)
    assert "greet()" in repr(job)


def test_oracle_repr_kwargs():
    job = schedule.every().hour.do(lambda: 1, name="x")
    assert "name='x'" in repr(job)
