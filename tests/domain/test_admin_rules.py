"""Админка: разбор запроса «кого искать», предел ручной выдачи и до какой даты продлить."""

from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.admin import (
    ClientQuery,
    GrantDaysOutOfRange,
    check_grant_days,
    manual_grant_target,
    parse_client_query,
)
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import MAX_MANUAL_GRANT_DAYS

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def snapshot(
    expire_at: datetime | None,
    *,
    on_trial: bool = False,
    grace_started_at: datetime | None = None,
    access_disabled: bool = False,
) -> ClientSnapshot:
    return ClientSnapshot(
        expire_at=expire_at,
        on_trial=on_trial,
        grace_started_at=grace_started_at,
        access_disabled=access_disabled,
    )


# --- Кого искать ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("123456789", ClientQuery(telegram_id=123456789)),
        ("  42 ", ClientQuery(telegram_id=42)),
        ("@Ivan_Petrov", ClientQuery(username="ivan_petrov")),
        ("ivan_petrov", ClientQuery(username="ivan_petrov")),
    ],
)
def test_query_by_id_or_username(text: str, expected: ClientQuery) -> None:
    assert parse_client_query(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   ", "0", "-5", "@", "@1abc", "иван", "ivan petrov", "12ab", "a" * 33, "9" * 20],
)
def test_unreadable_query(text: str) -> None:
    assert parse_client_query(text) is None


# --- Предел ручной выдачи (аудит R22) ---


@pytest.mark.parametrize("days", [1, 30, MAX_MANUAL_GRANT_DAYS])
def test_grant_days_within_limit(days: int) -> None:
    check_grant_days(days)


@pytest.mark.parametrize("days", [0, -1, MAX_MANUAL_GRANT_DAYS + 1])
def test_grant_days_out_of_limit(days: int) -> None:
    with pytest.raises(GrantDaysOutOfRange):
        check_grant_days(days)


# --- До какой даты продлить ---


def test_new_client_gets_days_from_now() -> None:
    assert manual_grant_target(snapshot(None), NOW, 7) == NOW + timedelta(days=7)


def test_active_subscription_gets_days_added_to_its_end() -> None:
    expire_at = NOW + timedelta(days=10)
    assert manual_grant_target(snapshot(expire_at), NOW, 30) == expire_at + timedelta(days=30)


def test_trial_gets_days_added_to_its_end() -> None:
    expire_at = NOW + timedelta(days=2)
    target = manual_grant_target(snapshot(expire_at, on_trial=True), NOW, 5)
    assert target == expire_at + timedelta(days=5)


def test_expired_subscription_gets_days_from_now() -> None:
    expire_at = NOW - timedelta(days=20)
    target = manual_grant_target(snapshot(expire_at, access_disabled=True), NOW, 30)
    assert target == NOW + timedelta(days=30)


def test_grace_gets_days_from_now() -> None:
    started = NOW - timedelta(days=1)
    grace_expire = started + timedelta(days=3)
    target = manual_grant_target(snapshot(grace_expire, grace_started_at=started), NOW, 30)
    assert target == NOW + timedelta(days=30)


def test_short_grant_in_grace_never_shortens_access() -> None:
    """1 день в льготный период, когда льготных осталось 2: срок не сокращается."""
    started = NOW - timedelta(days=1)
    grace_expire = started + timedelta(days=3)
    target = manual_grant_target(snapshot(grace_expire, grace_started_at=started), NOW, 1)
    assert target == grace_expire


def test_grant_days_checked_in_target() -> None:
    with pytest.raises(GrantDaysOutOfRange):
        manual_grant_target(snapshot(None), NOW, MAX_MANUAL_GRANT_DAYS + 1)
