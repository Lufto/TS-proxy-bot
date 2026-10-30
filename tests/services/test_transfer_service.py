"""Перенос подписки на другой Telegram-аккаунт (задача 21): только по подтверждённому
платежу, с предпросмотром и проверкой, что сроки не изменились."""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.content.loader import ADMIN_TEXTS_FILE, load_texts
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus, RefundStatus
from tsproxy.db.models import Client, Event, Order, RefundRequest
from tsproxy.domain.constants import (
    BYTES_PER_GB,
    PAID_DEVICE_LIMIT,
    TRIAL_DEVICE_LIMIT,
    UNLIMITED_TRAFFIC_BYTES,
)
from tsproxy.domain.refund import REFUND_END_MARGIN
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus
from tsproxy.services.alerts import Alerter
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.transfer import (
    TransferDone,
    TransferPreview,
    TransferRefusal,
    TransferRefused,
    TransferService,
)

FROM_ID = 111
TO_ID = 222
ADMIN_ID = 900
PAYMENT_ID = "fake_payment_1"
ADMIN_TEXTS = Path(__file__).parent.parent.parent / "content" / ADMIN_TEXTS_FILE


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
    mask: Masker,
) -> TransferService:
    texts = load_texts(ADMIN_TEXTS)
    alerter = Alerter(FakeAlertSender(), texts, -100500, 7, clock, mask)
    return TransferService(
        session_factory, remnawave, EventLog(clock, mask), alerter, texts, clock, KeyedLocks()
    )


@pytest.fixture
async def order(session: AsyncSession, client: Client, clock: FakeClock) -> Order:
    order = Order(
        client_id=client.id,
        tariff_id="m1",
        price=Decimal(150),
        days=30,
        status=OrderStatus.ACCESS_GRANTED,
        payment_provider="fake",
        payment_id=PAYMENT_ID,
        paid_at=clock(),
        paid_amount=Decimal(150),
        grant_target_expire_at=clock() + timedelta(days=30),
    )
    session.add(order)
    session.add(Client(telegram_id=TO_ID, first_seen_at=clock()))
    await session.commit()
    return order


async def paid_account(remnawave: FakeRemnawave, telegram_id: int, expire_at: datetime) -> None:
    await remnawave.get_or_create_account(
        telegram_id,
        expire_at=expire_at,
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )


async def test_preview_then_transfer_moves_remaining_time(
    service: TransferService,
    remnawave: FakeRemnawave,
    order: Order,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    expire_at = clock() + timedelta(days=20)
    await paid_account(remnawave, FROM_ID, expire_at)

    preview = await service.preview(FROM_ID, TO_ID, PAYMENT_ID)
    assert isinstance(preview, TransferPreview)
    assert (preview.order_id, preview.to_expire_at, preview.new_expire_at) == (
        order.id,
        None,
        expire_at,
    )

    done = await service.transfer(
        ADMIN_ID, order.id, TO_ID, expected_from=expire_at, expected_to=None
    )

    assert done == TransferDone(FROM_ID, expire_at)
    target = await remnawave.get_account(TO_ID)
    source = await remnawave.get_account(FROM_ID)
    assert (target.expire_at, target.device_limit) == (expire_at, PAID_DEVICE_LIMIT)
    assert source.status is AccountStatus.DISABLED
    assert source.expire_at == clock() + REFUND_END_MARGIN
    async with session_factory() as session:
        events = list(
            await session.scalars(select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value))
        )
    assert len(events) == 2
    assert {(e.details or {})["action"] for e in events} == {"subscription_transferred"}


async def test_second_confirm_does_nothing(
    service: TransferService, remnawave: FakeRemnawave, order: Order, clock: FakeClock
) -> None:
    expire_at = clock() + timedelta(days=20)
    await paid_account(remnawave, FROM_ID, expire_at)
    await service.transfer(ADMIN_ID, order.id, TO_ID, expected_from=expire_at, expected_to=None)

    again = await service.transfer(
        ADMIN_ID, order.id, TO_ID, expected_from=expire_at, expected_to=None
    )

    assert isinstance(again, TransferRefused)
    assert (await remnawave.get_account(TO_ID)).expire_at == expire_at


async def test_trial_target_gets_paid_limits_and_days_added(
    service: TransferService, remnawave: FakeRemnawave, order: Order, clock: FakeClock
) -> None:
    await paid_account(remnawave, FROM_ID, clock() + timedelta(days=20))
    await remnawave.get_or_create_account(
        TO_ID,
        expire_at=clock() + timedelta(days=3),
        device_limit=TRIAL_DEVICE_LIMIT,
        traffic_limit_bytes=7 * BYTES_PER_GB,
    )

    preview = await service.preview(FROM_ID, TO_ID, PAYMENT_ID)
    assert isinstance(preview, TransferPreview)
    await service.transfer(
        ADMIN_ID,
        order.id,
        TO_ID,
        expected_from=preview.from_expire_at,
        expected_to=preview.to_expire_at,
    )

    target = await remnawave.get_account(TO_ID)
    assert target.expire_at == clock() + timedelta(days=23)
    assert target.traffic_limit_bytes == UNLIMITED_TRAFFIC_BYTES


async def test_only_with_confirmed_payment_of_the_source(
    service: TransferService, remnawave: FakeRemnawave, order: Order, clock: FakeClock
) -> None:
    await paid_account(remnawave, FROM_ID, clock() + timedelta(days=20))

    assert await service.preview(FROM_ID, TO_ID, "fake_other") == TransferRefused(
        TransferRefusal.PAYMENT_NOT_FOUND
    )
    # платёж есть, но он не у того аккаунта, с которого переносим
    assert await service.preview(TO_ID, FROM_ID, PAYMENT_ID) == TransferRefused(
        TransferRefusal.PAYMENT_NOT_FOUND
    )


@pytest.mark.parametrize(
    ("from_id", "to_id", "refusal"),
    [
        (FROM_ID, FROM_ID, TransferRefusal.SAME_CLIENT),
        (333, TO_ID, TransferRefusal.SOURCE_NOT_FOUND),
        (FROM_ID, 333, TransferRefusal.TARGET_NOT_FOUND),
    ],
)
async def test_both_must_be_bot_clients(
    service: TransferService, order: Order, from_id: int, to_id: int, refusal: TransferRefusal
) -> None:
    assert await service.preview(from_id, to_id, PAYMENT_ID) == TransferRefused(refusal)


async def test_nothing_to_transfer_after_term_ended(
    service: TransferService, remnawave: FakeRemnawave, order: Order, clock: FakeClock
) -> None:
    await paid_account(remnawave, FROM_ID, clock() + timedelta(days=1))
    clock.advance(timedelta(days=2))

    assert await service.preview(FROM_ID, TO_ID, PAYMENT_ID) == TransferRefused(
        TransferRefusal.NOTHING_TO_TRANSFER
    )


async def test_not_while_refund_is_open(
    service: TransferService,
    remnawave: FakeRemnawave,
    order: Order,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await paid_account(remnawave, FROM_ID, clock() + timedelta(days=20))
    session.add(
        RefundRequest(
            order_id=order.id,
            client_id=client.id,
            unused_days=30,
            amount=Decimal(150),
            status=RefundStatus.IN_REVIEW,
        )
    )
    await session.commit()

    assert await service.preview(FROM_ID, TO_ID, PAYMENT_ID) == TransferRefused(
        TransferRefusal.REFUND_OPEN
    )
