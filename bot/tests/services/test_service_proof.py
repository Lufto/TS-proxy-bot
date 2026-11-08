"""Доказательство оказания услуги: заказ, оплата, события клиента с момента заказа, панель."""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import NOW
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import Device, RemnawaveUnavailable
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock
from tsproxy.services.service_proof import PROOF_EVENTS_LIMIT, ServiceProofService

TELEGRAM_ID = 111
ORDER_AT = NOW - timedelta(days=3)


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


async def make_order(session: AsyncSession, client: Client) -> Order:
    order = Order(
        client_id=client.id,
        tariff_id="m1",
        price=Decimal("150.00"),
        days=30,
        status=OrderStatus.ACCESS_GRANTED,
        created_at=ORDER_AT,
        paid_at=ORDER_AT + timedelta(minutes=2),
        paid_amount=Decimal("150.00"),
        payment_provider="fake",
        payment_id="pay-1",
    )
    session.add(order)
    await session.flush()
    return order


async def test_collects_order_events_and_panel_facts(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: Client,
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    order = await make_order(session, client)
    other_client = Client(telegram_id=222, first_seen_at=NOW)
    session.add(other_client)
    await session.flush()
    session.add_all(
        [
            # до заказа и без ссылки на заказ — не нужно
            Event(
                kind=EventKind.TRIAL_GRANTED.value,
                client_id=client.id,
                occurred_at=NOW - timedelta(days=9),
            ),
            Event(
                kind=EventKind.ORDER_CREATED.value,
                client_id=client.id,
                order_id=order.id,
                occurred_at=ORDER_AT,
            ),
            Event(
                kind=EventKind.ACCESS_GRANTED.value,
                client_id=client.id,
                order_id=order.id,
                occurred_at=ORDER_AT + timedelta(minutes=3),
            ),
            # после заказа по тому же клиенту — нужно (действие администратора)
            Event(
                kind=EventKind.ADMIN_ACTION.value,
                client_id=client.id,
                admin_id=900,
                occurred_at=NOW,
                details={"action": "extend"},
            ),
            # чужой клиент — не нужно
            Event(kind=EventKind.ADMIN_ACTION.value, client_id=other_client.id, occurred_at=NOW),
        ]
    )
    await session.commit()
    await remnawave.get_or_create_account(
        TELEGRAM_ID, expire_at=NOW + timedelta(days=27), device_limit=3, traffic_limit_bytes=0
    )
    remnawave.simulate_connection(TELEGRAM_ID, ORDER_AT + timedelta(minutes=10))
    remnawave.simulate_device(
        TELEGRAM_ID,
        Device(
            hwid="h1", platform="iOS", os_version="18.1", device_model="iPhone", created_at=ORDER_AT
        ),
    )

    proof = await ServiceProofService(session_factory, remnawave, clock).collect(order.id)

    assert proof is not None
    assert proof.order.payment_id == "pay-1"
    assert proof.client.telegram_id == TELEGRAM_ID
    assert [e.kind for e in proof.events] == ["order_created", "access_granted", "admin_action"]
    assert proof.panel.available
    assert proof.panel.account is not None
    assert proof.panel.account.first_connected_at == ORDER_AT + timedelta(minutes=10)
    assert [d.hwid for d in proof.panel.devices] == ["h1"]


async def test_unknown_order(
    session_factory: async_sessionmaker[AsyncSession], remnawave: FakeRemnawave, clock: FakeClock
) -> None:
    assert await ServiceProofService(session_factory, remnawave, clock).collect(404) is None


async def test_panel_down_still_gives_database_facts(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: Client,
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    order = await make_order(session, client)
    await session.commit()
    remnawave.fail_next(RemnawaveUnavailable("нет связи"))

    proof = await ServiceProofService(session_factory, remnawave, clock).collect(order.id)

    assert proof is not None
    assert not proof.panel.available
    assert proof.panel.account is None


async def test_no_account_in_panel_is_a_fact(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: Client,
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    order = await make_order(session, client)
    await session.commit()

    proof = await ServiceProofService(session_factory, remnawave, clock).collect(order.id)

    assert proof is not None
    assert proof.panel.available
    assert proof.panel.account is None


async def test_events_are_capped(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    client: Client,
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    order = await make_order(session, client)
    session.add_all(
        Event(kind=EventKind.ADMIN_ACTION.value, client_id=client.id, occurred_at=NOW)
        for _ in range(PROOF_EVENTS_LIMIT + 5)
    )
    await session.commit()

    proof = await ServiceProofService(session_factory, remnawave, clock).collect(order.id)

    assert proof is not None
    assert len(proof.events) == PROOF_EVENTS_LIMIT
    assert proof.truncated
