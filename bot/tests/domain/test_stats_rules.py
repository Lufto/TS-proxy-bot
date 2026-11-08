"""Правила статистики админки (задача 19): границы дня и месяца по Москве, конверсия,
время ответа поддержки, подсчёт подписок по состояниям."""

from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.stats import (
    ResponseTimes,
    SubscriptionCounts,
    conversion_percent,
    moscow_day_start,
    moscow_month_start,
    response_times,
    split_minutes,
    tally_phases,
)
from tsproxy.domain.subscription import SubscriptionPhase


def test_day_starts_at_moscow_midnight() -> None:
    # 23:30 UTC 24.10 — это уже 02:30 МСК 25.10: «сегодня» началось в 21:00 UTC 24.10
    now = datetime(2026, 10, 24, 23, 30, tzinfo=UTC)
    assert moscow_day_start(now) == datetime(2026, 10, 24, 21, 0, tzinfo=UTC)


def test_day_start_before_moscow_midnight_is_previous_utc_evening() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)  # 15:00 МСК
    assert moscow_day_start(now) == datetime(2026, 10, 23, 21, 0, tzinfo=UTC)


def test_month_starts_on_first_day_in_moscow() -> None:
    # 31.10 22:00 UTC — уже 1 ноября по Москве
    now = datetime(2026, 10, 31, 22, 0, tzinfo=UTC)
    assert moscow_month_start(now) == datetime(2026, 10, 31, 21, 0, tzinfo=UTC)
    assert moscow_month_start(datetime(2026, 10, 24, 12, 0, tzinfo=UTC)) == datetime(
        2026, 9, 30, 21, 0, tzinfo=UTC
    )


def test_period_start_needs_timezone() -> None:
    with pytest.raises(ValueError, match="пояса"):
        moscow_day_start(datetime(2026, 10, 24, 12, 0))  # noqa: DTZ001  # проверяем отказ


def test_conversion_is_rounded_down() -> None:
    assert conversion_percent(trials=3, converted=1) == 33
    assert conversion_percent(trials=3, converted=2) == 66
    assert conversion_percent(trials=4, converted=4) == 100


def test_conversion_without_trials_is_unknown() -> None:
    assert conversion_percent(trials=0, converted=0) is None


def test_conversion_rejects_impossible_numbers() -> None:
    with pytest.raises(ValueError, match="конверси"):
        conversion_percent(trials=1, converted=2)
    with pytest.raises(ValueError, match="конверси"):
        conversion_percent(trials=-1, converted=0)


def test_response_times_are_average_and_longest() -> None:
    times = response_times([timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=2)])
    assert times == ResponseTimes(
        average=timedelta(minutes=160 / 3), longest=timedelta(hours=2), count=3
    )


def test_no_answers_no_response_times() -> None:
    assert response_times([]) is None


def test_negative_response_time_is_error() -> None:
    with pytest.raises(ValueError, match="отрицательн"):
        response_times([timedelta(minutes=-1)])


def test_split_minutes_drops_seconds() -> None:
    assert split_minutes(timedelta(hours=1, minutes=5, seconds=59)) == (1, 5)
    assert split_minutes(timedelta(seconds=40)) == (0, 0)
    assert split_minutes(timedelta(hours=26)) == (26, 0)


def test_tally_counts_only_live_subscriptions() -> None:
    phases = [
        SubscriptionPhase.ACTIVE,
        SubscriptionPhase.ACTIVE,
        SubscriptionPhase.GRACE,
        SubscriptionPhase.TRIAL,
        SubscriptionPhase.EXPIRED,
        SubscriptionPhase.NEW,
    ]
    assert tally_phases(phases) == SubscriptionCounts(paid=2, grace=1, trial=1)
