"""Правила задачи 14: случайная двойная оплата и ежедневная сверка с платёжкой."""

from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.payments import (
    OrderRecord,
    ProviderRecord,
    ReconcileIssue,
    ReconcileMismatch,
    possible_double_payment,
    reconcile_payments,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

# --- Случайная двойная оплата (решение владельца 2026-10-25, п. 1) ---


def test_other_order_credited_after_this_one_was_created_is_double() -> None:
    """Две ссылки жили одновременно: клиент мог заплатить по обеим случайно."""
    assert possible_double_payment(NOW, NOW + timedelta(minutes=5))


def test_other_order_credited_before_this_one_is_a_new_purchase() -> None:
    """Сначала «✅ Оплачено», потом новый заказ — клиент покупает сознательно."""
    assert not possible_double_payment(NOW, NOW - timedelta(minutes=1))


def test_no_other_credited_order_is_not_double() -> None:
    assert not possible_double_payment(NOW, None)


def test_naive_dates_are_rejected() -> None:
    with pytest.raises(ValueError):
        possible_double_payment(NOW.replace(tzinfo=None), NOW)


# --- Сверка с платёжкой (решение владельца 2026-10-25, п. 4) ---


def paid(payment_id: str, *, refunded: bool = False) -> ProviderRecord:
    return ProviderRecord(payment_id, paid=True, refunded=refunded)


def order(
    order_id: int, payment_id: str, *, credited: bool = True, refunded: bool = False
) -> OrderRecord:
    return OrderRecord(order_id, payment_id, credited=credited, refunded=refunded)


def test_everything_matches() -> None:
    provider = [paid("p1"), ProviderRecord("p2", paid=False, refunded=False)]
    ours = [order(1, "p1"), order(2, "p2", credited=False)]

    assert reconcile_payments(provider, ours) == []


def test_payment_without_order() -> None:
    """Деньги у платёжки, а заказа нет (уязвимость 6)."""
    assert reconcile_payments([paid("p1")], []) == [
        ReconcileIssue(ReconcileMismatch.PAID_WITHOUT_ORDER, "p1", None)
    ]


def test_paid_but_not_credited() -> None:
    """Уведомление потерялось, проверки не нашли (уязвимость 2)."""
    assert reconcile_payments([paid("p1")], [order(7, "p1", credited=False)]) == [
        ReconcileIssue(ReconcileMismatch.PAID_NOT_CREDITED, "p1", 7)
    ]


def test_credited_but_provider_says_not_paid() -> None:
    provider = [ProviderRecord("p1", paid=False, refunded=False)]

    assert reconcile_payments(provider, [order(7, "p1")]) == [
        ReconcileIssue(ReconcileMismatch.CREDITED_NOT_PAID, "p1", 7)
    ]


def test_credited_order_missing_from_provider_list() -> None:
    """Сервис перепроверит такой платёж по одному: список мог не захватить его по времени."""
    assert reconcile_payments([], [order(7, "p1"), order(8, "p2", credited=False)]) == [
        ReconcileIssue(ReconcileMismatch.MISSING_AT_PROVIDER, "p1", 7)
    ]


def test_refund_outside_bot() -> None:
    """Возвраты — только через бота (решение владельца, п. 4)."""
    assert reconcile_payments([paid("p1", refunded=True)], [order(7, "p1")]) == [
        ReconcileIssue(ReconcileMismatch.REFUNDED_OUTSIDE_BOT, "p1", 7)
    ]


def test_refund_made_by_bot_matches() -> None:
    provider = [paid("p1", refunded=True)]

    assert reconcile_payments(provider, [order(7, "p1", credited=False, refunded=True)]) == []
