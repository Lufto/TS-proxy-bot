from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tsproxy.domain.refund import (
    RefundDenial,
    RefundReason,
    RefundRequest,
    decide_refund,
    refund_amount,
    unused_full_days,
)

PAID_AT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
DAYS_30 = timedelta(days=30)


def request(
    now: datetime,
    *,
    reason: RefundReason = RefundReason.SERVICE_FAULT,
    paid_amount: Decimal = Decimal("150"),
    period_start: datetime = PAID_AT,
    period_end: datetime = PAID_AT + DAYS_30,
    last_refund_at: datetime | None = None,
) -> RefundRequest:
    return RefundRequest(
        now=now,
        reason=reason,
        paid_at=PAID_AT,
        paid_amount=paid_amount,
        period_start=period_start,
        period_end=period_end,
        last_refund_at=last_refund_at,
    )


# --- Неиспользованные полные дни и сумма ---


def test_unused_full_days_drops_partial_day() -> None:
    now = PAID_AT + timedelta(days=10, hours=12)
    assert unused_full_days(PAID_AT, PAID_AT + DAYS_30, now) == 19


def test_unused_days_of_period_that_has_not_started_yet() -> None:
    # Купил при активной подписке: оплаченный срок начнётся позже, все дни не использованы
    start = PAID_AT + timedelta(days=5)
    assert unused_full_days(start, start + DAYS_30, PAID_AT) == 30


def test_unused_days_after_period_end_is_zero() -> None:
    assert unused_full_days(PAID_AT, PAID_AT + DAYS_30, PAID_AT + timedelta(days=31)) == 0


def test_amount_for_unused_days() -> None:
    now = PAID_AT + timedelta(days=10, hours=12)
    assert refund_amount(Decimal("150"), PAID_AT, PAID_AT + DAYS_30, now) == Decimal("95.00")


def test_amount_three_month_tariff() -> None:
    now = PAID_AT + timedelta(days=7, hours=1)
    end = PAID_AT + timedelta(days=90)
    assert refund_amount(Decimal("405"), PAID_AT, end, now) == Decimal("369.00")


def test_amount_rounds_down_to_kopecks() -> None:
    # Срок после пропорционального пересчёта: 482 часа = 20,083 дня, не использовано 20
    end = PAID_AT + timedelta(hours=482)
    assert refund_amount(Decimal("100"), PAID_AT, end, PAID_AT) == Decimal("99.58")


def test_amount_full_when_nothing_used() -> None:
    assert refund_amount(Decimal("150"), PAID_AT, PAID_AT + DAYS_30, PAID_AT) == Decimal("150.00")


def test_amount_never_exceeds_paid() -> None:
    start = PAID_AT + timedelta(days=5)
    amount = refund_amount(Decimal("150"), start, start + DAYS_30, PAID_AT)
    assert amount <= Decimal("150")


def test_amount_is_decimal_with_two_places() -> None:
    amount = refund_amount(Decimal("150"), PAID_AT, PAID_AT + DAYS_30, PAID_AT + timedelta(days=3))
    assert isinstance(amount, Decimal)
    assert amount == Decimal("135.00")
    assert amount.as_tuple().exponent == -2


def test_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        unused_full_days(PAID_AT, PAID_AT, PAID_AT)


# --- Можно ли вернуть (задача 12) ---


def test_refund_allowed_for_service_fault() -> None:
    decision = decide_refund(request(PAID_AT + timedelta(days=3)))
    assert decision.allowed
    assert decision.denial is None
    assert decision.unused_days == 27
    assert decision.amount == Decimal("135.00")


@pytest.mark.parametrize(
    ("reason", "denial"),
    [
        (RefundReason.PROVIDER_BLOCK, RefundDenial.BLOCKED_BY_PROVIDER),
        (RefundReason.STATE_BLOCK, RefundDenial.BLOCKED_BY_STATE),
        (RefundReason.CHANGED_MIND, RefundDenial.CHANGED_MIND),
    ],
)
def test_no_refund_for_reasons_outside_our_fault(
    reason: RefundReason, denial: RefundDenial
) -> None:
    decision = decide_refund(request(PAID_AT + timedelta(days=1), reason=reason))
    assert not decision.allowed
    assert decision.denial is denial


def test_refund_at_exactly_30_days_is_allowed() -> None:
    end = PAID_AT + timedelta(days=90)
    decision = decide_refund(request(PAID_AT + timedelta(days=30), period_end=end))
    assert decision.allowed
    assert decision.unused_days == 60


def test_refund_one_second_after_30_days_is_denied() -> None:
    end = PAID_AT + timedelta(days=90)
    now = PAID_AT + timedelta(days=30, seconds=1)
    decision = decide_refund(request(now, period_end=end))
    assert decision.denial is RefundDenial.WINDOW_PASSED


def test_second_refund_within_six_months_denied() -> None:
    last = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    decision = decide_refund(request(PAID_AT + timedelta(days=1), last_refund_at=last))
    assert decision.denial is RefundDenial.TOO_FREQUENT


def test_refund_exactly_six_months_after_previous_is_allowed() -> None:
    last = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    assert decide_refund(request(now, last_refund_at=last)).allowed


def test_refund_one_second_before_six_months_is_denied() -> None:
    last = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    now = datetime(2026, 11, 2, 11, 59, 59, tzinfo=UTC)
    assert decide_refund(request(now, last_refund_at=last)).denial is RefundDenial.TOO_FREQUENT


def test_nothing_left_to_refund() -> None:
    end = PAID_AT + timedelta(hours=20)
    decision = decide_refund(request(PAID_AT + timedelta(hours=1), period_end=end))
    assert decision.denial is RefundDenial.NOTHING_UNUSED
    assert decision.amount == Decimal("0.00")


def test_refund_rejects_naive_now() -> None:
    with pytest.raises(ValueError):
        decide_refund(request(datetime(2026, 11, 2)))  # noqa: DTZ001
