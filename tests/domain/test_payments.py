from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.constants import PAYMENT_LINK_LIFETIME
from tsproxy.domain.payments import (
    PAYMENT_REUSE_MIN_REMAINING,
    can_reuse_payment,
    payment_link_expires_at,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

# --- Сколько живёт ссылка на оплату (задача 04, правило 3) ---


def test_link_lives_one_hour_when_provider_says_nothing() -> None:
    assert payment_link_expires_at(NOW, None) == NOW + PAYMENT_LINK_LIFETIME


def test_link_lives_one_hour_even_if_provider_allows_longer() -> None:
    assert payment_link_expires_at(NOW, NOW + timedelta(days=1)) == NOW + PAYMENT_LINK_LIFETIME


def test_provider_shorter_lifetime_wins() -> None:
    soon = NOW + timedelta(minutes=20)
    assert payment_link_expires_at(NOW, soon) == soon


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="часового пояса"):
        payment_link_expires_at(NOW.replace(tzinfo=None), None)


# --- Отдать ту же ссылку ещё раз ---


def test_fresh_link_is_reused() -> None:
    assert can_reuse_payment(NOW + timedelta(minutes=59), NOW)


def test_link_about_to_expire_is_not_reused() -> None:
    # клиент не успеет оплатить — лучше новая ссылка
    almost = NOW + PAYMENT_REUSE_MIN_REMAINING - timedelta(seconds=1)
    assert not can_reuse_payment(almost, NOW)


def test_link_with_exactly_min_remaining_is_reused() -> None:
    assert can_reuse_payment(NOW + PAYMENT_REUSE_MIN_REMAINING, NOW)


def test_expired_link_is_not_reused() -> None:
    assert not can_reuse_payment(NOW - timedelta(minutes=1), NOW)


def test_link_without_expiry_is_not_reused() -> None:
    assert not can_reuse_payment(None, NOW)
