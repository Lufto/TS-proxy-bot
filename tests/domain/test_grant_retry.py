"""Повторы выдачи доступа после оплаты (задача 04 п. 6, задача 21 п. 7, аудит §7.2)."""

from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.constants import (
    GRANT_FAST_RETRIES,
    GRANT_FAST_RETRY_INTERVAL,
    GRANT_RETRY_DEADLINE,
    GRANT_SLOW_RETRY_INTERVAL,
)
from tsproxy.domain.grant import plan_grant_retry

PAID_AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def test_first_half_hour_retries_every_minute() -> None:
    now = PAID_AT + timedelta(seconds=5)

    retry = plan_grant_retry(PAID_AT, failed_attempts=1, now=now)

    assert retry.next_attempt_at == now + GRANT_FAST_RETRY_INTERVAL
    assert not retry.escalate


def test_last_fast_retry_is_still_one_minute() -> None:
    now = PAID_AT + timedelta(minutes=29)

    retry = plan_grant_retry(PAID_AT, failed_attempts=GRANT_FAST_RETRIES, now=now)

    assert retry.next_attempt_at == now + GRANT_FAST_RETRY_INTERVAL
    assert not retry.escalate


def test_after_half_hour_alert_once_and_retry_every_ten_minutes() -> None:
    now = PAID_AT + timedelta(minutes=30)

    first_slow = plan_grant_retry(PAID_AT, failed_attempts=GRANT_FAST_RETRIES + 1, now=now)
    later = plan_grant_retry(PAID_AT, failed_attempts=GRANT_FAST_RETRIES + 2, now=now)

    assert first_slow.next_attempt_at == now + GRANT_SLOW_RETRY_INTERVAL
    assert first_slow.escalate
    assert later.next_attempt_at == now + GRANT_SLOW_RETRY_INTERVAL
    assert not later.escalate


def test_last_attempt_happens_exactly_at_the_deadline() -> None:
    now = PAID_AT + GRANT_RETRY_DEADLINE - timedelta(minutes=3)

    retry = plan_grant_retry(PAID_AT, failed_attempts=100, now=now)

    assert retry.next_attempt_at == PAID_AT + GRANT_RETRY_DEADLINE


def test_after_a_day_retries_stop() -> None:
    retry = plan_grant_retry(PAID_AT, failed_attempts=100, now=PAID_AT + GRANT_RETRY_DEADLINE)

    assert retry.next_attempt_at is None
    assert not retry.escalate


def test_deadline_is_one_day() -> None:
    assert timedelta(days=1) == GRANT_RETRY_DEADLINE


def test_attempts_must_be_counted_from_one() -> None:
    with pytest.raises(ValueError, match="попыт"):
        plan_grant_retry(PAID_AT, failed_attempts=0, now=PAID_AT)


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="часового пояса"):
        plan_grant_retry(PAID_AT, failed_attempts=1, now=PAID_AT.replace(tzinfo=None))
