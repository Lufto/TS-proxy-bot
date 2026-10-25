"""Заказ и платёж (задача 12): цена из файла тарифов, одна ссылка на двойное нажатие."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.content.loader import Tariff, Texts
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.domain.constants import PAYMENT_LINK_LIFETIME
from tsproxy.domain.payments import PAYMENT_REUSE_MIN_REMAINING
from tsproxy.integrations.fake_payments import FAKE_PROVIDER_NAME, FakePaymentProvider
from tsproxy.integrations.payments_types import PaymentUnavailable
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.orders import (
    NEW_PAYMENTS_LIMIT,
    NEW_PAYMENTS_WINDOW,
    OrderService,
    PaymentsDisabled,
    TariffUnavailable,
    TooManyPayments,
)

ADMIN_TELEGRAM_ID = 900
TARIFFS = (
    Tariff("m1", "1 месяц", 30, Decimal("150")),
    Tariff("m3", "3 месяца", 90, Decimal("405")),
    Tariff("m6", "6 месяцев", 180, Decimal("765")),
    Tariff("m12", "12 месяцев", 365, Decimal("1440")),
    Tariff("test15", "Тест — 1 день", 1, Decimal("15"), hidden=True),
)
TEXTS = Texts({"payment.description": "TS Proxy, {title}"}, "test")


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def payments(clock: FakeClock) -> FakePaymentProvider:
    return FakePaymentProvider(clock)


def make_service(
    session_factory: async_sessionmaker[AsyncSession],
    payments: FakePaymentProvider | None,
    clock: FakeClock,
    mask: Masker,
    tariffs: tuple[Tariff, ...] = TARIFFS,
) -> OrderService:
    return OrderService(
        session_factory,
        payments,
        EventLog(clock, mask),
        TEXTS,
        tariffs,
        frozenset({ADMIN_TELEGRAM_ID}),
        clock,
        KeyedLocks(),
    )


@pytest.fixture
def orders(
    session_factory: async_sessionmaker[AsyncSession],
    payments: FakePaymentProvider,
    clock: FakeClock,
    mask: Masker,
) -> OrderService:
    return make_service(session_factory, payments, clock, mask)


@pytest.fixture
async def admin(session: AsyncSession, clock: FakeClock) -> Client:
    admin = Client(telegram_id=ADMIN_TELEGRAM_ID, first_seen_at=clock())
    session.add(admin)
    await session.commit()
    return admin


async def all_orders(session_factory: async_sessionmaker[AsyncSession]) -> list[Order]:
    async with session_factory() as session:
        return list(await session.scalars(select(Order).order_by(Order.id)))


async def event_kinds(session_factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with session_factory() as session:
        return list(await session.scalars(select(Event.kind).order_by(Event.id)))


# --- Витрина тарифов ---


def test_client_sees_visible_tariffs_with_discounts(orders: OrderService) -> None:
    offers = orders.offers(telegram_id=111)

    assert [(o.tariff.id, o.discount_percent) for o in offers] == [
        ("m1", 0),
        ("m3", 10),
        ("m6", 15),
        ("m12", 20),
    ]


def test_admin_also_sees_hidden_test_tariff(orders: OrderService) -> None:
    offers = orders.offers(telegram_id=ADMIN_TELEGRAM_ID)

    assert [o.tariff.id for o in offers] == ["m1", "m3", "m6", "m12", "test15"]
    assert offers[-1].discount_percent == 0


def test_no_monthly_tariff_means_no_discounts(
    session_factory: async_sessionmaker[AsyncSession],
    payments: FakePaymentProvider,
    clock: FakeClock,
    mask: Masker,
) -> None:
    service = make_service(session_factory, payments, clock, mask, tariffs=TARIFFS[1:4])
    assert [o.discount_percent for o in service.offers(telegram_id=111)] == [0, 0, 0]


# --- Заказ и платёж ---


async def test_checkout_creates_order_with_price_from_tariffs(
    orders: OrderService,
    payments: FakePaymentProvider,
    client: Client,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    checkout = await orders.checkout(client, "m3")

    [order] = await all_orders(session_factory)
    assert (order.client_id, order.tariff_id, order.price, order.days) == (
        client.id,
        "m3",
        Decimal("405"),
        90,
    )
    assert order.status is OrderStatus.CREATED
    assert order.created_at == clock()
    assert order.payment_provider == FAKE_PROVIDER_NAME
    assert order.payment_url == checkout.payment_url
    assert order.payment_url_expires_at == clock() + PAYMENT_LINK_LIFETIME
    [payment] = payments.ledger.all()
    assert (payment.payment_id, payment.amount, payment.order_id) == (
        order.payment_id,
        Decimal("405"),
        order.id,
    )
    assert payment.idempotency_key == order.idempotency_key
    assert payment.description == "TS Proxy, 3 месяца"
    assert (checkout.order_id, checkout.tariff.id, checkout.reused) == (order.id, "m3", False)
    assert checkout.expires_at == order.payment_url_expires_at
    assert await event_kinds(session_factory) == [
        EventKind.ORDER_CREATED,
        EventKind.PAYMENT_CREATED,
    ]


async def test_double_press_gives_one_payment(
    orders: OrderService,
    payments: FakePaymentProvider,
    client: Client,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await orders.checkout(client, "m3")
    clock.advance(timedelta(minutes=30))
    second = await orders.checkout(client, "m3")

    assert second.payment_url == first.payment_url
    assert second.order_id == first.order_id
    assert second.reused
    assert len(payments.ledger.all()) == 1
    assert len(await all_orders(session_factory)) == 1


async def test_simultaneous_double_press_gives_one_payment(
    orders: OrderService,
    payments: FakePaymentProvider,
    client: Client,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first, second = await asyncio.gather(
        orders.checkout(client, "m1"), orders.checkout(client, "m1")
    )

    assert first.payment_url == second.payment_url
    assert len(payments.ledger.all()) == 1
    assert len(await all_orders(session_factory)) == 1


async def test_other_tariff_gives_new_payment(
    orders: OrderService, payments: FakePaymentProvider, client: Client
) -> None:
    m1 = await orders.checkout(client, "m1")
    m3 = await orders.checkout(client, "m3")
    m1_again = await orders.checkout(client, "m1")

    assert m1.payment_url != m3.payment_url
    assert m1_again.payment_url == m1.payment_url  # ссылка на первый тариф ещё живёт
    assert len(payments.ledger.all()) == 2


async def test_other_client_gets_own_payment(
    orders: OrderService, payments: FakePaymentProvider, client: Client, admin: Client
) -> None:
    mine = await orders.checkout(client, "m1")
    theirs = await orders.checkout(admin, "m1")

    assert mine.payment_url != theirs.payment_url
    assert len(payments.ledger.all()) == 2


async def test_link_about_to_expire_is_replaced(
    orders: OrderService, payments: FakePaymentProvider, client: Client, clock: FakeClock
) -> None:
    first = await orders.checkout(client, "m1")
    clock.advance(PAYMENT_LINK_LIFETIME - PAYMENT_REUSE_MIN_REMAINING + timedelta(seconds=1))
    second = await orders.checkout(client, "m1")

    assert second.payment_url != first.payment_url
    assert second.order_id != first.order_id
    assert not second.reused


async def test_changed_price_is_not_reused(
    orders: OrderService,
    payments: FakePaymentProvider,
    client: Client,
    clock: FakeClock,
    mask: Masker,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await orders.checkout(client, "m1")
    cheaper = (Tariff("m1", "1 месяц", 30, Decimal("120")), *TARIFFS[1:])
    restarted = make_service(session_factory, payments, clock, mask, tariffs=cheaper)

    checkout = await restarted.checkout(client, "m1")

    assert not checkout.reused
    assert [o.price for o in await all_orders(session_factory)] == [Decimal("150"), Decimal("120")]


async def test_unknown_tariff_is_refused(
    orders: OrderService, client: Client, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(TariffUnavailable):
        await orders.checkout(client, "m100")
    assert await all_orders(session_factory) == []


async def test_hidden_tariff_is_refused_for_client(
    orders: OrderService,
    payments: FakePaymentProvider,
    client: Client,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Подменённая кнопка со скрытым тарифом: проверка в сервисе, а не только в клавиатуре."""
    with pytest.raises(TariffUnavailable):
        await orders.checkout(client, "test15")
    assert await all_orders(session_factory) == []
    assert payments.ledger.all() == []


async def test_hidden_tariff_for_admin(orders: OrderService, admin: Client) -> None:
    checkout = await orders.checkout(admin, "test15")
    assert checkout.tariff.price == Decimal("15")


async def test_provider_down_then_retry_uses_same_order_and_key(
    orders: OrderService,
    payments: FakePaymentProvider,
    client: Client,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payments.fail_next(PaymentUnavailable("таймаут"))
    with pytest.raises(PaymentUnavailable):
        await orders.checkout(client, "m1")

    [order] = await all_orders(session_factory)
    assert order.payment_id is None

    checkout = await orders.checkout(client, "m1")

    [order] = await all_orders(session_factory)
    assert checkout.order_id == order.id
    [payment] = payments.ledger.all()
    assert payment.idempotency_key == order.idempotency_key
    assert order.payment_id == payment.payment_id


async def test_too_many_new_payments(
    session_factory: async_sessionmaker[AsyncSession],
    payments: FakePaymentProvider,
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    many = tuple(
        Tariff(f"t{n}", f"Тариф {n}", 30, Decimal(100 + n)) for n in range(NEW_PAYMENTS_LIMIT + 1)
    )
    service = make_service(session_factory, payments, clock, mask, tariffs=many)
    for tariff in many[:-1]:
        await service.checkout(client, tariff.id)
        clock.advance(timedelta(seconds=10))

    await service.checkout(client, many[0].id)  # та же ссылка ещё раз — не новый платёж
    with pytest.raises(TooManyPayments):
        await service.checkout(client, many[-1].id)

    clock.advance(NEW_PAYMENTS_WINDOW)
    await service.checkout(client, many[-1].id)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == len(many)


# --- Оплата выключена (задача 29) ---


def test_payments_enabled_only_with_provider(
    session_factory: async_sessionmaker[AsyncSession],
    payments: FakePaymentProvider,
    clock: FakeClock,
    mask: Masker,
) -> None:
    assert make_service(session_factory, payments, clock, mask).payments_enabled
    assert not make_service(session_factory, None, clock, mask).payments_enabled


def test_tariffs_are_shown_while_payments_off(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker
) -> None:
    """Витрина та же: клиент видит цены, хотя купить пока нельзя."""
    service = make_service(session_factory, None, clock, mask)

    assert [o.tariff.id for o in service.offers(telegram_id=111)] == ["m1", "m3", "m6", "m12"]


async def test_checkout_while_payments_off_creates_nothing(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    """«Готово, когда» задачи 29: «Купить» не создаёт ни заказ, ни платёж."""
    service = make_service(session_factory, None, clock, mask)

    with pytest.raises(PaymentsDisabled):
        await service.checkout(client, "m1")

    assert await all_orders(session_factory) == []
    assert await event_kinds(session_factory) == []


async def test_payments_off_is_checked_before_tariff(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    """Старая кнопка несуществующего тарифа — тоже «оплата выключена», а не «нет тарифа»."""
    service = make_service(session_factory, None, clock, mask)

    with pytest.raises(PaymentsDisabled):
        await service.checkout(client, "m100")
