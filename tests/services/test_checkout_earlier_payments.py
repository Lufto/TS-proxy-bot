"""«Купить» сначала ищет прошлые оплаты (задача 14, уязвимость 1 карты оплаты: двойная оплата)."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from payment_helpers import PaymentSetup, build_payment_setup
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.content.loader import Tariff, Texts
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client
from tsproxy.domain.constants import RECENT_PAYMENTS_LIMIT, RECENT_PAYMENTS_WINDOW
from tsproxy.integrations.payments_types import PaymentUnavailable
from tsproxy.services.events import EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.orders import EarlierPaymentFound, OrderService
from tsproxy.services.payment_checks import CheckOutcome

TARIFFS = (
    Tariff("m1", "1 месяц", 30, Decimal("150")),
    Tariff("m3", "3 месяца", 90, Decimal("405")),
)
TEXTS = Texts({"payment.description": "TS Proxy, {title}"}, "test")


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def s(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker
) -> PaymentSetup:
    return build_payment_setup(session_factory, clock, mask)


@pytest.fixture
def orders(s: PaymentSetup, mask: Masker) -> OrderService:
    return OrderService(
        s.session_factory,
        s.payments,
        EventLog(s.clock, mask),
        TEXTS,
        TARIFFS,
        s.clock,
        KeyedLocks(),
        s.checks,
    )


async def test_lost_notification_then_buy_credits_and_gives_no_new_link(
    s: PaymentSetup, orders: OrderService, client: Client
) -> None:
    """П. 7 рекомендации: оплатил → уведомление потеряно → через 61 минуту «Купить»."""
    checkout = await orders.checkout(client, "m1")
    s.payments.ledger.pay(await s.payment_id(checkout.order_id), s.clock())
    s.clock.advance(timedelta(minutes=61))

    with pytest.raises(EarlierPaymentFound) as found:
        await orders.checkout(client, "m1")

    assert found.value.order_id == checkout.order_id
    assert (await s.load(checkout.order_id)).status is OrderStatus.ACCESS_GRANTED
    assert len(s.payments.ledger.all()) == 1  # новый платёж не создан
    assert len(s.messages.granted) == 1


async def test_paid_link_is_not_shown_again(
    s: PaymentSetup, orders: OrderService, client: Client
) -> None:
    """Та же ссылка ещё жива, но по ней уже заплатили — засчитать, а не показывать снова."""
    checkout = await orders.checkout(client, "m1")
    s.payments.ledger.pay(await s.payment_id(checkout.order_id), s.clock())

    with pytest.raises(EarlierPaymentFound):
        await orders.checkout(client, "m1")


async def test_payment_of_other_tariff_is_found_too(
    s: PaymentSetup, orders: OrderService, client: Client
) -> None:
    checkout = await orders.checkout(client, "m3")
    s.payments.ledger.pay(await s.payment_id(checkout.order_id), s.clock())

    with pytest.raises(EarlierPaymentFound):
        await orders.checkout(client, "m1")


async def test_provider_silent_on_check_gives_no_new_link(
    s: PaymentSetup, orders: OrderService, client: Client
) -> None:
    """Платёжка не ответила на проверку прошлых оплат — новой ссылки нет."""
    await orders.checkout(client, "m1")
    s.payments.fail_next(PaymentUnavailable("таймаут"))

    with pytest.raises(PaymentUnavailable):
        await orders.checkout(client, "m3")

    assert len(s.payments.ledger.all()) == 1


async def test_unpaid_earlier_orders_do_not_block_new_link(
    s: PaymentSetup, orders: OrderService, client: Client
) -> None:
    first = await orders.checkout(client, "m1")

    second = await orders.checkout(client, "m3")

    assert second.order_id != first.order_id
    assert s.payments.asked == [await s.payment_id(first.order_id)]


async def test_only_three_freshest_orders_of_last_day_are_checked(
    s: PaymentSetup, orders: OrderService, client: Client
) -> None:
    day_old = await s.order(client, paid=True)
    s.clock.advance(RECENT_PAYMENTS_WINDOW)
    oldest_recent = await s.order(client, paid=True)
    for _ in range(RECENT_PAYMENTS_LIMIT):
        s.clock.advance(timedelta(minutes=1))
        await s.order(client, status=OrderStatus.CANCELED)

    await orders.checkout(client, "m1")  # оплаты среди трёх свежих нет — новая ссылка

    assert await s.payment_id(day_old) not in s.payments.asked
    assert await s.payment_id(oldest_recent) not in s.payments.asked
    assert len(s.payments.asked) == RECENT_PAYMENTS_LIMIT


async def test_checkout_and_notification_at_once_credit_once(
    s: PaymentSetup, orders: OrderService, client: Client
) -> None:
    """Замки «заказы → срок» не встают друг другу поперёк: обе задачи завершаются."""
    checkout = await orders.checkout(client, "m1")
    payment_id = await s.payment_id(checkout.order_id)
    s.payments.ledger.pay(payment_id, s.clock())

    results = await asyncio.wait_for(
        asyncio.gather(
            orders.checkout(client, "m1"),
            s.checks.handle_notification(payment_id, None),
            return_exceptions=True,
        ),
        timeout=5,
    )

    assert isinstance(results[0], EarlierPaymentFound)
    assert results[1] in (CheckOutcome.CREDITED, CheckOutcome.ALREADY_CREDITED)
    assert len(s.messages.granted) == 1
