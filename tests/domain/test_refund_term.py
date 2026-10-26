"""Задача 21: возврат случайной второй оплаты, предел по остатку срока и укорачивание срока."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tsproxy.domain.constants import DOUBLE_PAYMENT_REFUND_DAYS
from tsproxy.domain.refund import (
    REFUND_END_MARGIN,
    RefundDecision,
    RefundDenial,
    decide_double_payment_refund,
    limit_to_term,
    next_refund_at,
    term_after_refund,
)

PAID_AT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
MONTH = timedelta(days=30)


# --- Случайная вторая оплата (решение владельца 2026-10-25, п. 1) ---


def test_double_payment_refunds_everything_within_seven_days() -> None:
    now = PAID_AT + timedelta(days=DOUBLE_PAYMENT_REFUND_DAYS)
    decision = decide_double_payment_refund(now, PAID_AT, Decimal("150"), MONTH)
    assert decision.allowed
    assert decision.amount == Decimal("150.00")
    assert decision.unused_days == 30


def test_double_payment_after_seven_days_is_denied() -> None:
    now = PAID_AT + timedelta(days=DOUBLE_PAYMENT_REFUND_DAYS, seconds=1)
    decision = decide_double_payment_refund(now, PAID_AT, Decimal("150"), MONTH)
    assert decision.denial is RefundDenial.WINDOW_PASSED


def test_double_payment_prorated_term_counts_only_full_days() -> None:
    # оплачено не на сумму тарифа: срок 15 дней 2 часа — укорачиваем на 15 полных дней
    duration = timedelta(days=15, hours=2)
    decision = decide_double_payment_refund(PAID_AT, PAID_AT, Decimal("75"), duration)
    assert decision.unused_days == 15
    assert decision.amount == Decimal("75.00")


def test_double_payment_rejects_naive_now() -> None:
    with pytest.raises(ValueError):
        decide_double_payment_refund(datetime(2026, 11, 2), PAID_AT, Decimal("150"), MONTH)  # noqa: DTZ001


# --- Не больше дней, чем осталось в сроке ---


def test_limit_keeps_decision_when_term_is_long_enough() -> None:
    decision = RefundDecision(None, 20, Decimal("100.00"))
    assert limit_to_term(decision, PAID_AT + timedelta(days=25), PAID_AT) == decision


def test_limit_cuts_days_and_amount_to_what_is_left() -> None:
    # срок уже укоротили (перенос, администратор) — возвращаем только оставшиеся дни
    decision = RefundDecision(None, 20, Decimal("100.00"))
    limited = limit_to_term(decision, PAID_AT + timedelta(days=5, hours=3), PAID_AT)
    assert limited == RefundDecision(None, 5, Decimal("25.00"))


def test_limit_to_empty_term_means_nothing_unused() -> None:
    decision = RefundDecision(None, 20, Decimal("100.00"))
    limited = limit_to_term(decision, PAID_AT - timedelta(hours=1), PAID_AT)
    assert limited.denial is RefundDenial.NOTHING_UNUSED
    assert limited.amount == Decimal("0.00")


def test_limit_keeps_earlier_denial() -> None:
    decision = RefundDecision(RefundDenial.WINDOW_PASSED, 20, Decimal("100.00"))
    limited = limit_to_term(decision, PAID_AT + timedelta(days=2), PAID_AT)
    assert limited.denial is RefundDenial.WINDOW_PASSED
    assert limited.unused_days == 2


# --- Укорачивание срока после возврата (payment-journeys №8, Г5) ---


def test_term_shortened_by_refunded_days_keeps_other_orders() -> None:
    # после этого заказа куплен ещё один: отнимаем только дни возвращённого
    expire_at = PAID_AT + timedelta(days=60)
    after = term_after_refund(expire_at, 20, PAID_AT)
    assert after.expire_at == PAID_AT + timedelta(days=40)
    assert not after.end_access


def test_term_ends_now_when_less_than_a_day_would_remain() -> None:
    # остаётся неполный день возвращённого заказа — доступ выключается сразу,
    # иначе по окончании начался бы льготный период (3 + 1 бесплатных дня)
    expire_at = PAID_AT + timedelta(days=20, hours=5)
    after = term_after_refund(expire_at, 20, PAID_AT)
    assert after.end_access
    assert after.expire_at == PAID_AT + REFUND_END_MARGIN


def test_term_end_never_moves_later() -> None:
    expire_at = PAID_AT + timedelta(seconds=10)
    after = term_after_refund(expire_at, 0, PAID_AT)
    assert after.end_access
    assert after.expire_at == expire_at


def test_term_after_refund_rejects_negative_days() -> None:
    with pytest.raises(ValueError):
        term_after_refund(PAID_AT, -1, PAID_AT)


# --- Когда можно следующий возврат ---


def test_next_refund_is_six_calendar_months_later() -> None:
    last = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    assert next_refund_at(last) == datetime(2027, 2, 28, 9, 0, tzinfo=UTC)
