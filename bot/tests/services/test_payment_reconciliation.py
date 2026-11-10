"""Ежедневная сверка оплат с платёжкой (задача 14; решение владельца 2026-10-25, п. 4)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from payment_helpers import ADMIN_CHAT_ID, ALERTS_THREAD_ID, PaymentSetup, build_payment_setup
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client
from tsproxy.domain.constants import PAYMENTS_RECONCILE_MARGIN
from tsproxy.integrations.payments_types import (
    PaymentRejected,
    PaymentRequest,
    PaymentStatus,
    PaymentUnavailable,
)
from tsproxy.services.alerts import Alerter
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.payment_reconciliation import PaymentReconciliation


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def s(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker
) -> PaymentSetup:
    return build_payment_setup(session_factory, clock, mask)


def reconciliation(
    s: PaymentSetup, mask: Masker, *, payments_off: bool = False
) -> PaymentReconciliation:
    return PaymentReconciliation(
        s.session_factory,
        None if payments_off else s.payments,
        s.checks,
        Alerter(s.alerts, s.admin_texts, ADMIN_CHAT_ID, ALERTS_THREAD_ID, s.clock, mask),
        s.admin_texts,
        s.clock,
    )


def next_day(s: PaymentSetup) -> None:
    s.clock.advance(PAYMENTS_RECONCILE_MARGIN + timedelta(hours=1))


async def test_matching_payments_raise_no_alert(
    s: PaymentSetup, mask: Masker, client: Client
) -> None:
    paid = await s.order(client, paid=True)
    await s.crediting.credit(paid, await s.payments.get_payment(await s.payment_id(paid)))
    await s.order(client)  # не оплачен — и у нас не засчитан
    next_day(s)

    await reconciliation(s, mask).run()

    assert s.alerts.sent == []


async def test_missed_payment_is_credited_and_reported(
    s: PaymentSetup, mask: Masker, client: Client
) -> None:
    """Оплату пропустили уведомление и проверки — сверка засчитывает и говорит об этом."""
    order_id = await s.order(client, paid=True)
    await s.set_status(order_id, OrderStatus.CANCELED)
    next_day(s)

    await reconciliation(s, mask).run()

    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED
    [alert] = s.alerts.sent
    assert "расхождений — 1" in alert.text
    assert f"#{order_id}" in alert.text
    assert "засчитала его сейчас" in alert.text


async def test_payment_without_order_is_reported(
    s: PaymentSetup, mask: Masker, client: Client
) -> None:
    """Уязвимость 6: деньги у платёжки, а заказа у нас нет."""
    created = await s.payments.create_payment(PaymentRequest(999, Decimal("150"), "x", "lost"))
    s.payments.ledger.pay(created.payment_id, s.clock())
    next_day(s)

    await reconciliation(s, mask).run()

    [alert] = s.alerts.sent
    assert created.payment_id in alert.text


async def test_refund_outside_bot_is_reported(
    s: PaymentSetup, mask: Masker, client: Client
) -> None:
    """Возвраты — только через бота: возврат в кабинете платёжки ловит сверка."""
    order_id = await s.order(client, paid=True)
    payment_id = await s.payment_id(order_id)
    await s.crediting.credit(order_id, await s.payments.get_payment(payment_id))
    s.payments.ledger.refund(payment_id, Decimal("150"), "by-hand")
    next_day(s)

    await reconciliation(s, mask).run()

    [alert] = s.alerts.sent
    assert f"#{order_id}" in alert.text
    assert "возврат" in alert.text


async def test_credited_order_unknown_to_provider_is_reported(
    s: PaymentSetup, mask: Masker, client: Client
) -> None:
    order_id = await s.order(client, paid=True)
    await s.crediting.credit(order_id, await s.payments.get_payment(await s.payment_id(order_id)))
    s.payments.ledger = type(s.payments.ledger)()  # платёжка «забыла» платёж
    next_day(s)

    await reconciliation(s, mask).run()

    [alert] = s.alerts.sent
    assert f"#{order_id}" in alert.text


async def test_provider_down_raises_alert(s: PaymentSetup, mask: Masker) -> None:
    s.payments.fail_next(PaymentUnavailable("таймаут"))

    await reconciliation(s, mask).run()

    [alert] = s.alerts.sent
    assert s.admin_texts.get("alert.payments_reconcile_failed.title") in alert.text
    assert "таймаут" in alert.text
    assert s.admin_texts.get("failure.payments.unavailable.causes.1.cause") in alert.text


async def test_provider_refusal_raises_alert_with_its_text(s: PaymentSetup, mask: Masker) -> None:
    """Задача 15: выгрузка Platega ответила не по документации — тревога с её ответом."""
    s.payments.fail_next(PaymentRejected("выгрузка Platega вернула не список платежей"))

    await reconciliation(s, mask).run()

    [alert] = s.alerts.sent
    assert "не список платежей" in alert.text


async def test_chargeback_outside_bot_is_listed(
    s: PaymentSetup, mask: Masker, client: Client
) -> None:
    """Задача 15: деньги вернули мимо бота (спор через банк) — строка «возврат мимо бота»."""
    order_id = await s.order(client, paid=True)
    payment_id = await s.payment_id(order_id)
    await s.crediting.credit(order_id, await s.payments.get_payment(payment_id))
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)
    next_day(s)

    await reconciliation(s, mask).run()

    [alert] = s.alerts.sent
    assert "возврат" in alert.text and f"#{order_id}" in alert.text


async def test_nothing_to_reconcile_when_payments_off(s: PaymentSetup, mask: Masker) -> None:
    await reconciliation(s, mask, payments_off=True).run()

    assert s.alerts.sent == []
