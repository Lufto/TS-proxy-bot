"""Возвраты через поддержку (задача 21, решение владельца 2026-10-30): какие оплаты можно
вернуть и новый срок после возврата."""

from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.refund import (
    REFUND_END_MARGIN,
    refund_list_since,
    refund_window_open,
    term_after_refund,
)

PAID_AT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
MONTH = timedelta(days=30)


# --- Оплаты за последние 30 дней ---


def test_payment_exactly_30_days_old_can_be_refunded() -> None:
    assert refund_window_open(PAID_AT, PAID_AT + timedelta(days=30))


def test_payment_older_than_30_days_cannot_be_refunded() -> None:
    assert not refund_window_open(PAID_AT, PAID_AT + timedelta(days=30, seconds=1))


def test_refund_list_starts_30_days_ago() -> None:
    now = PAID_AT + timedelta(days=45)
    assert refund_list_since(now) == now - timedelta(days=30)


def test_refund_window_rejects_naive_time() -> None:
    with pytest.raises(ValueError):
        refund_window_open(PAID_AT, datetime(2026, 11, 2))  # noqa: DTZ001


# --- Срок после возврата: минус всё, что дала эта оплата ---


def test_term_loses_exactly_what_the_refunded_payment_gave() -> None:
    # после этой оплаты куплена ещё одна: её дни остаются
    after = term_after_refund(PAID_AT + timedelta(days=60), MONTH, PAID_AT)
    assert after.expire_at == PAID_AT + timedelta(days=30)
    assert not after.end_access


def test_prorated_payment_takes_back_its_hours_too() -> None:
    # оплачено не на сумму тарифа: оплата дала 15 дней 2 часа — столько и снимаем
    given = timedelta(days=15, hours=2)
    after = term_after_refund(PAID_AT + timedelta(days=40), given, PAID_AT)
    assert after.expire_at == PAID_AT + timedelta(days=24, hours=22)


def test_used_days_of_refunded_payment_end_access_now() -> None:
    # клиент пользовался 10 дней из 30 и вернул всю сумму: доступ кончается сразу
    now = PAID_AT + timedelta(days=10)
    after = term_after_refund(PAID_AT + MONTH, MONTH, now)
    assert after.end_access
    assert after.expire_at == now + REFUND_END_MARGIN


def test_access_ends_now_when_less_than_a_day_would_remain() -> None:
    # остался бы неполный день — иначе по окончании начался бы льготный период
    after = term_after_refund(PAID_AT + timedelta(days=30, hours=5), MONTH, PAID_AT)
    assert after.end_access
    assert after.expire_at == PAID_AT + REFUND_END_MARGIN


def test_term_end_never_moves_later() -> None:
    expire_at = PAID_AT + timedelta(seconds=10)
    after = term_after_refund(expire_at, timedelta(0), PAID_AT)
    assert after.end_access
    assert after.expire_at == expire_at


def test_negative_duration_is_a_bug() -> None:
    with pytest.raises(ValueError):
        term_after_refund(PAID_AT, timedelta(days=-1), PAID_AT)
