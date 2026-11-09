"""Оплата звёздами Telegram (задача 33): счёт, проверка перед оплатой, зачёт ровно один раз
по `telegram_payment_charge_id`, возврат лишней оплаты. Telegram и панель — подделки."""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import pytest
from payment_helpers import PaymentSetup, build_payment_setup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tsproxy.content.loader import Tariff, Texts
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.domain.stars import STARS_CURRENCY, STARS_INVOICE_LIFETIME, StarsRefusal
from tsproxy.integrations.payments_types import (
    PaidPayment,
    PaymentRejected,
    Refund,
    RefundOutcome,
)
from tsproxy.integrations.telegram_stars import STARS_PROVIDER
from tsproxy.services.crediting import STARS_DETAIL
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock, FakeEarlierPayments
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.orders import OrderService, PaymentsDisabled, TariffUnavailable
from tsproxy.services.stars import StarsPaid, StarsPayment, StarsPayments

RATE = Decimal("1.5")
ADMIN_ID = 900
TARIFFS = (
    Tariff("m1", "1 месяц", 30, Decimal("150")),
    Tariff("m3", "3 месяца", 90, Decimal("405")),
)
CHARGE = "stxCharge-1"


@dataclass
class FakeStarsRefunds:
    """Возврат звёзд — запоминаем вместо Telegram; `fail` — Telegram отказал."""

    refunded: list[PaidPayment] = field(default_factory=list)
    fail: bool = False

    async def refund(self, payment: PaidPayment, amount: Decimal, idempotency_key: str) -> Refund:
        if self.fail:
            raise PaymentRejected("CHARGE_NOT_FOUND")
        self.refunded.append(payment)
        return Refund(payment.payment_id, payment.payment_id, amount, RefundOutcome.SUCCEEDED)


@dataclass
class Setup:
    stars: StarsPayments
    pay: PaymentSetup
    refunds: FakeStarsRefunds
    clock: FakeClock

    def payment(
        self, order_id: int, *, stars: int = 100, charge: str = CHARGE, telegram_id: int = 111
    ) -> StarsPayment:
        return StarsPayment(telegram_id, str(order_id), STARS_CURRENCY, stars, charge)


@pytest.fixture
def setup(engine: AsyncEngine, clock: FakeClock, mask: Masker) -> Setup:
    session_factory = create_session_factory(engine)
    pay = build_payment_setup(session_factory, clock, mask)
    events = EventLog(clock, mask)
    orders = OrderService(
        session_factory,
        pay.payments,
        events,
        Texts({"payment.description": "TS Proxy, {title}"}, "test"),
        TARIFFS,
        clock,
        KeyedLocks(),
        FakeEarlierPayments(),
    )
    refunds = FakeStarsRefunds()
    stars = StarsPayments(
        session_factory,
        orders,
        pay.crediting,
        refunds,
        events,
        pay.alerter,
        pay.admin_texts,
        clock,
        RATE,
    )
    return Setup(stars, pay, refunds, clock)


async def load(setup: Setup, order_id: int) -> Order:
    return await setup.pay.load(order_id)


# --- Счёт ---


def test_prices_in_stars_follow_rate(setup: Setup) -> None:
    assert setup.stars.price(TARIFFS[0]) == 100
    assert setup.stars.price(TARIFFS[1]) == 270


async def test_invoice_creates_stars_order_with_payload(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m3")

    order = await load(setup, invoice.order_id)
    assert (invoice.stars, invoice.payload, invoice.tariff.id) == (270, str(order.id), "m3")
    assert order.payment_provider == STARS_PROVIDER
    assert (order.status, order.price, order.payment_id) == (OrderStatus.CREATED, 405, None)
    assert setup.pay.payments.ledger.all() == []  # платёжку СБП не трогали


async def test_each_press_is_new_order(setup: Setup, client: Client) -> None:
    first = await setup.stars.invoice(client, "m1")
    second = await setup.stars.invoice(client, "m1")

    assert first.order_id != second.order_id


async def test_unknown_tariff_is_not_sold_for_stars(setup: Setup, session: AsyncSession) -> None:
    admin = Client(telegram_id=ADMIN_ID)
    session.add(admin)
    await session.commit()

    with pytest.raises(TariffUnavailable):
        await setup.stars.invoice(admin, "test15")  # тестового тарифа больше нет (задача 15)


async def test_sbp_checkout_does_not_reuse_stars_order(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")

    checkout = await setup.stars._orders.checkout(client, "m1")

    assert checkout.order_id != invoice.order_id
    assert (await load(setup, invoice.order_id)).payment_id is None


async def test_payments_off_means_no_stars(
    engine: AsyncEngine, clock: FakeClock, mask: Masker, client: Client
) -> None:
    session_factory = create_session_factory(engine)
    pay = build_payment_setup(session_factory, clock, mask)
    orders = OrderService(
        session_factory,
        None,
        EventLog(clock, mask),
        Texts({}, "test"),
        TARIFFS,
        clock,
        KeyedLocks(),
        FakeEarlierPayments(),
    )
    stars = StarsPayments(
        session_factory,
        orders,
        pay.crediting,
        FakeStarsRefunds(),
        EventLog(clock, mask),
        pay.alerter,
        pay.admin_texts,
        clock,
        RATE,
    )
    with pytest.raises(PaymentsDisabled):
        await stars.invoice(client, "m1")


# --- Проверка перед оплатой ---


async def test_pre_checkout_ok_for_live_order(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")

    assert await setup.stars.pre_checkout(111, invoice.payload, STARS_CURRENCY, 100) is None


async def test_pre_checkout_refuses_wrong_amount(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")

    refusal = await setup.stars.pre_checkout(111, invoice.payload, STARS_CURRENCY, 99)

    assert refusal is StarsRefusal.AMOUNT_MISMATCH


async def test_pre_checkout_refuses_old_invoice(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")
    setup.clock.advance(STARS_INVOICE_LIFETIME)

    refusal = await setup.stars.pre_checkout(111, invoice.payload, STARS_CURRENCY, 100)

    assert refusal is StarsRefusal.EXPIRED


@pytest.mark.parametrize("payload", ["999", "abc", "", "-1"])
async def test_pre_checkout_refuses_unknown_payload(
    setup: Setup, client: Client, payload: str
) -> None:
    await setup.stars.invoice(client, "m1")

    refusal = await setup.stars.pre_checkout(111, payload, STARS_CURRENCY, 100)

    assert refusal is StarsRefusal.NOT_FOUND


async def test_pre_checkout_refuses_other_clients_invoice(setup: Setup, client: Client) -> None:
    # пересланный счёт: платит не тот, для кого он выставлен
    invoice = await setup.stars.invoice(client, "m1")

    refusal = await setup.stars.pre_checkout(222, invoice.payload, STARS_CURRENCY, 100)

    assert refusal is StarsRefusal.NOT_FOUND


async def test_pre_checkout_refuses_sbp_order(setup: Setup, client: Client) -> None:
    order_id = await setup.pay.order(client)

    refusal = await setup.stars.pre_checkout(111, str(order_id), STARS_CURRENCY, 100)

    assert refusal is StarsRefusal.NOT_FOUND


async def test_pre_checkout_refuses_paid_order(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")
    await setup.stars.paid(setup.payment(invoice.order_id))

    refusal = await setup.stars.pre_checkout(111, invoice.payload, STARS_CURRENCY, 100)

    assert refusal is StarsRefusal.ALREADY_PAID


# --- Оплата пришла ---


async def test_payment_is_credited_and_access_granted(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")

    outcome = await setup.stars.paid(setup.payment(invoice.order_id))

    assert outcome is StarsPaid.CREDITED
    order = await load(setup, invoice.order_id)
    assert (order.status, order.payment_id) == (OrderStatus.ACCESS_GRANTED, CHARGE)
    assert order.paid_amount == Decimal("150")  # цена заказа в рублях — срок ровно 30 дней
    [granted] = setup.pay.messages.granted
    assert granted.account.expire_at == setup.clock() + timedelta(days=30)
    [received] = await setup.pay.events(EventKind.PAYMENT_RECEIVED.value)
    assert received.details is not None
    assert (received.details["provider"], received.details[STARS_DETAIL]) == (STARS_PROVIDER, 100)


async def test_payments_topic_marks_stars(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")

    await setup.stars.paid(setup.payment(invoice.order_id))

    [posted] = setup.pay.topic.sent
    assert "150 ₽" in posted.text
    assert setup.pay.admin_texts.get("payments.stars", stars=100) in posted.text


async def test_repeated_update_credits_once(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")
    payment = setup.payment(invoice.order_id)

    first = await setup.stars.paid(payment)
    second = await setup.stars.paid(payment)

    assert (first, second) == (StarsPaid.CREDITED, StarsPaid.ALREADY_CREDITED)
    assert len(setup.pay.messages.granted) == 1
    assert len(await setup.pay.events(EventKind.PAYMENT_RECEIVED.value)) == 1
    assert setup.refunds.refunded == []


async def test_second_charge_for_paid_order_is_returned(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")
    await setup.stars.paid(setup.payment(invoice.order_id))

    outcome = await setup.stars.paid(setup.payment(invoice.order_id, charge="stxCharge-2"))

    assert outcome is StarsPaid.RETURNED
    assert setup.refunds.refunded == [PaidPayment(STARS_PROVIDER, "stxCharge-2", 111)]
    assert (await load(setup, invoice.order_id)).payment_id == CHARGE
    assert len(setup.pay.messages.granted) == 1
    [alert] = setup.pay.alerts.sent
    assert "stxCharge-2" not in alert.text and "100 ⭐" in alert.text
    [event] = await setup.pay.events(EventKind.STARS_PAYMENT_RETURNED.value)
    assert event.details is not None and event.details["returned"] is True


async def test_payment_for_unknown_order_is_returned(setup: Setup, client: Client) -> None:
    outcome = await setup.stars.paid(setup.payment(999))

    assert outcome is StarsPaid.RETURNED
    assert [p.payment_id for p in setup.refunds.refunded] == [CHARGE]


async def test_failed_return_alerts_admins(setup: Setup, client: Client) -> None:
    setup.refunds.fail = True

    outcome = await setup.stars.paid(setup.payment(999))

    assert outcome is StarsPaid.NOT_RETURNED
    [alert] = setup.pay.alerts.sent
    assert CHARGE in alert.text
    [event] = await setup.pay.events(EventKind.STARS_PAYMENT_RETURNED.value)
    assert event.details is not None and event.details["returned"] is False


async def test_two_paid_invoices_are_double_payment(setup: Setup, client: Client) -> None:
    first = await setup.stars.invoice(client, "m1")
    second = await setup.stars.invoice(client, "m1")
    setup.clock.advance(timedelta(minutes=1))

    await setup.stars.paid(setup.payment(first.order_id))
    await setup.stars.paid(setup.payment(second.order_id, charge="stxCharge-2"))

    # дни сложены, клиенту — «две оплаты; случайную — через поддержку»
    assert setup.pay.messages.doubles == [111]


async def test_charge_is_unique_per_order(setup: Setup, client: Client) -> None:
    invoice = await setup.stars.invoice(client, "m1")
    await setup.stars.paid(setup.payment(invoice.order_id))

    async with setup.pay.session_factory() as session:
        charges = list(
            await session.scalars(
                select(Order.payment_id).where(Order.payment_provider == STARS_PROVIDER)
            )
        )
        events = list(await session.scalars(select(Event.kind)))
    assert charges == [CHARGE]
    assert EventKind.ORDER_CREATED.value in events
