from datetime import UTC, datetime, time, timedelta

import pytest

from tsproxy.domain.monitoring import (
    DAILY_JOB_STALE_AFTER,
    is_job_stale,
    next_daily_run,
    periodic_stale_after,
    previous_moscow_day,
)

# 10:00 МСК = 07:00 UTC
REPORT_AT = time(10, 0)


def test_next_daily_run_later_today() -> None:
    now = datetime(2026, 10, 25, 5, 0, tzinfo=UTC)  # 08:00 МСК
    assert next_daily_run(now, REPORT_AT) == datetime(2026, 10, 25, 7, 0, tzinfo=UTC)


def test_next_daily_run_already_passed_moves_to_tomorrow() -> None:
    now = datetime(2026, 10, 25, 7, 0, tzinfo=UTC)  # ровно 10:00 МСК — уже прошло
    assert next_daily_run(now, REPORT_AT) == datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


def test_next_daily_run_counts_moscow_date_not_utc() -> None:
    # 22:30 UTC 25.10 = 01:30 МСК 26.10 → 04:30 МСК 26.10 = 01:30 UTC 26.10
    now = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)
    assert next_daily_run(now, time(4, 30)) == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)


def test_next_daily_run_requires_timezone() -> None:
    with pytest.raises(ValueError, match="часового пояса"):
        next_daily_run(datetime(2026, 10, 25, 5, 0), REPORT_AT)  # noqa: DTZ001


def test_previous_moscow_day_bounds() -> None:
    now = datetime(2026, 10, 25, 7, 0, tzinfo=UTC)  # 10:00 МСК 25.10
    day = previous_moscow_day(now)
    assert day.date.isoformat() == "2026-10-24"
    # 00:00 МСК 24.10 = 21:00 UTC 23.10
    assert day.start == datetime(2026, 10, 23, 21, 0, tzinfo=UTC)
    assert day.end == datetime(2026, 10, 24, 21, 0, tzinfo=UTC)


def test_previous_moscow_day_just_after_moscow_midnight() -> None:
    now = datetime(2026, 10, 24, 21, 5, tzinfo=UTC)  # 00:05 МСК 25.10
    assert previous_moscow_day(now).date.isoformat() == "2026-10-24"


def test_periodic_job_is_stale_after_three_missed_runs() -> None:
    assert periodic_stale_after(timedelta(minutes=5)) == timedelta(minutes=15)


def test_short_jobs_get_at_least_a_few_minutes() -> None:
    # раз в минуту: минутная задержка Telegram не должна будить сторожа
    assert periodic_stale_after(timedelta(minutes=1)) == timedelta(minutes=5)


def test_daily_job_is_stale_after_26_hours() -> None:
    assert timedelta(hours=26) == DAILY_JOB_STALE_AFTER


START = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
LIMIT = timedelta(minutes=5)


def test_fresh_success_is_not_stale() -> None:
    assert not is_job_stale(
        START + timedelta(minutes=1), START, LIMIT, START + timedelta(minutes=5)
    )


def test_old_success_is_stale() -> None:
    now = START + timedelta(minutes=10)
    assert is_job_stale(START + timedelta(minutes=4), START, LIMIT, now)


def test_never_succeeded_counts_from_watch_start() -> None:
    assert not is_job_stale(None, START, LIMIT, START + timedelta(minutes=4))
    assert is_job_stale(None, START, LIMIT, START + timedelta(minutes=6))


def test_exactly_at_limit_is_not_stale_yet() -> None:
    assert not is_job_stale(START, START, LIMIT, START + LIMIT)
