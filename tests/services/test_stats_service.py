"""Статистика админки (задача 19): продажи, подписки, конверсия пробного, поддержка,
заблокировавшие бота. Нынешние клиенты панели (не `tg_`) в подписки не попадают."""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import NOW
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus, TicketStatus
from tsproxy.db.models import Client, Event, Order, SupportTicket
from tsproxy.domain.constants import (
    BYTES_PER_GB,
    PAID_DEVICE_LIMIT,
    TRIAL_DEVICE_LIMIT,
    TRIAL_TRAFFIC_GB,
    UNLIMITED_TRAFFIC_BYTES,
)
from tsproxy.domain.stats import ResponseTimes, SubscriptionCounts
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus, RemnawaveUnavailable
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock
from tsproxy.services.stats import RefundsTotal, SalesTotal, StatsService

# NOW = 24.10.2026 12:00 UTC = 15:00 МСК; день по Москве начался 23.10 в 21:00 UTC
TODAY_EARLY = NOW - timedelta(hours=14)  # 24.10 01:00 МСК — сегодня
YESTERDAY = NOW - timedelta(hours=16)  # 23.10 23:00 МСК — вчера, но в этом месяце
LAST_MONTH = NOW - timedelta(days=30)  # 24.09 — прошлый месяц
TRIAL_TRAFFIC = TRIAL_TRAFFIC_GB * BYTES_PER_GB


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def stats(
    session_factory: async_sessionmaker[AsyncSession], remnawave: FakeRemnawave, clock: FakeClock
) -> StatsService:
    return StatsService(session_factory, remnawave, clock)


async def add_client(session: AsyncSession, telegram_id: int, **fields: object) -> Client:
    client = Client(telegram_id=telegram_id, first_seen_at=LAST_MONTH, **fields)
    session.add(client)
    await session.commit()
    return client


async def add_paid_order(
    session: AsyncSession,
    client: Client,
    paid_at: object,
    price: str = "150",
    paid_amount: str | None = None,
) -> None:
    session.add(
        Order(
            client_id=client.id,
            tariff_id="m1",
            price=Decimal(price),
            days=30,
            status=OrderStatus.ACCESS_GRANTED,
            created_at=LAST_MONTH,
            paid_at=paid_at,
            paid_amount=None if paid_amount is None else Decimal(paid_amount),
        )
    )
    await session.commit()


async def trial_event(session: AsyncSession, client: Client, at: object) -> None:
    session.add(Event(occurred_at=at, kind=EventKind.TRIAL_GRANTED.value, client_id=client.id))
    await session.commit()


# --- Продажи ---


async def test_sales_today_and_this_month_by_moscow_calendar(
    stats: StatsService, session: AsyncSession
) -> None:
    client = await add_client(session, 111)
    await add_paid_order(session, client, TODAY_EARLY, price="150")
    await add_paid_order(session, client, YESTERDAY, price="405")
    await add_paid_order(session, client, LAST_MONTH, price="765")
    # неоплаченный заказ — не продажа
    session.add(Order(client_id=client.id, tariff_id="m1", price=Decimal(150), days=30))
    await session.commit()

    result = await stats.collect()

    assert result.sales_today == SalesTotal(count=1, amount=Decimal(150))
    assert result.sales_month == SalesTotal(count=2, amount=Decimal(555))


async def test_sales_count_what_was_actually_paid(
    stats: StatsService, session: AsyncSession
) -> None:
    client = await add_client(session, 111)
    await add_paid_order(session, client, TODAY_EARLY, price="150", paid_amount="100")

    result = await stats.collect()

    assert result.sales_today == SalesTotal(count=1, amount=Decimal(100))


# --- Подписки ---


async def test_subscriptions_by_state_without_manual_panel_clients(
    stats: StatsService, session: AsyncSession, remnawave: FakeRemnawave
) -> None:
    week = NOW + timedelta(days=7)
    paid = await add_client(session, 111)
    await add_client(session, 222, grace_started_at=NOW - timedelta(days=1))
    await add_client(session, 333, trial_granted_at=NOW - timedelta(days=1))
    await add_client(session, 444)
    for telegram_id in (111, 222):
        await remnawave.get_or_create_account(
            telegram_id,
            expire_at=week,
            device_limit=PAID_DEVICE_LIMIT,
            traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
        )
    await remnawave.get_or_create_account(
        333, expire_at=week, device_limit=TRIAL_DEVICE_LIMIT, traffic_limit_bytes=TRIAL_TRAFFIC
    )
    await remnawave.get_or_create_account(
        444,
        expire_at=week,
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )
    remnawave.set_status(444, AccountStatus.DISABLED)  # доступ выключен — не живая подписка
    # нынешние клиенты панели, заведённые вручную: бот их не считает
    for number in range(3):
        remnawave.add_foreign_account(f"manual_{number}", telegram_id=500 + number)
    assert paid.id is not None

    result = await stats.collect()

    assert result.subscriptions == SubscriptionCounts(paid=1, grace=1, trial=1)


async def test_subscriptions_unknown_when_panel_is_down(
    stats: StatsService, remnawave: FakeRemnawave
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("таймаут"))

    result = await stats.collect()

    assert result.subscriptions is None  # остальное из базы всё равно видно
    assert result.sales_today == SalesTotal(count=0, amount=Decimal(0))


# --- Конверсия пробного ---


async def test_trial_conversion_counts_payments_after_first_trial(
    stats: StatsService, session: AsyncSession
) -> None:
    converted = await add_client(session, 111, trial_granted_at=LAST_MONTH)
    await trial_event(session, converted, LAST_MONTH)
    await add_paid_order(session, converted, YESTERDAY)
    await add_paid_order(session, converted, TODAY_EARLY)  # второй платёж — тот же клиент
    still_trial = await add_client(session, 222, trial_granted_at=YESTERDAY)
    await trial_event(session, still_trial, YESTERDAY)
    # платил до пробного (пробный после сброса) — это не конверсия пробного
    paid_before = await add_client(session, 333, trial_granted_at=YESTERDAY)
    await add_paid_order(session, paid_before, LAST_MONTH)
    await trial_event(session, paid_before, YESTERDAY)
    # отметка пробного есть, а доставки (события) нет — пробного клиент не получил
    await add_client(session, 444, trial_granted_at=YESTERDAY)
    # покупатель без пробного не входит ни в числитель, ни в знаменатель
    buyer = await add_client(session, 555)
    await add_paid_order(session, buyer, YESTERDAY)

    result = await stats.collect()

    assert (result.trials.trials, result.trials.converted) == (3, 1)
    assert result.trials.percent == 33


# --- Поддержка ---


async def test_support_open_now_and_first_response_times(
    stats: StatsService, session: AsyncSession
) -> None:
    client = await add_client(session, 111)
    session.add_all(
        [
            # открыто и ждёт первого ответа
            SupportTicket(client_id=client.id, opened_at=NOW - timedelta(hours=1)),
            # открыто снова после закрытия: `closed_at` остаётся, открыто — по статусу
            SupportTicket(
                client_id=client.id,
                opened_at=YESTERDAY,
                first_response_at=YESTERDAY + timedelta(minutes=10),
                closed_at=YESTERDAY + timedelta(hours=1),
            ),
            SupportTicket(
                client_id=client.id,
                status=TicketStatus.CLOSED,
                opened_at=TODAY_EARLY,
                first_response_at=TODAY_EARLY + timedelta(minutes=50),
                closed_at=TODAY_EARLY + timedelta(hours=2),
            ),
            # прошлый месяц — во время ответа за месяц не входит
            SupportTicket(
                client_id=client.id,
                status=TicketStatus.CLOSED,
                opened_at=LAST_MONTH,
                first_response_at=LAST_MONTH + timedelta(days=1),
                closed_at=LAST_MONTH + timedelta(days=1),
            ),
        ]
    )
    await session.commit()

    support = (await stats.collect()).support

    assert support.open_now == 2
    assert support.waiting_first_reply == 1
    assert support.opened_month == 3
    assert support.response == ResponseTimes(
        average=timedelta(minutes=30), longest=timedelta(minutes=50), count=2
    )


# --- Клиенты ---


async def test_clients_total_and_blocked(stats: StatsService, session: AsyncSession) -> None:
    await add_client(session, 111)
    await add_client(session, 222, bot_blocked_at=YESTERDAY)

    result = await stats.collect()

    assert (result.clients.total, result.clients.blocked) == (2, 1)


# --- Возвраты (задача 21) ---


async def refund_event(session: AsyncSession, client: Client, at: object, amount: str) -> None:
    """Платёжка вернула деньги — так пишет `services.refunds` (событие с суммой)."""
    session.add(
        Event(
            occurred_at=at,
            kind=EventKind.REFUND_COMPLETED.value,
            client_id=client.id,
            details={"request_id": 1, "amount": amount},
        )
    )
    await session.commit()


async def test_refunds_are_subtracted_by_time_of_refund(
    stats: StatsService, session: AsyncSession
) -> None:
    client = await add_client(session, 111)
    await add_paid_order(session, client, LAST_MONTH, price="405")
    await add_paid_order(session, client, TODAY_EARLY, price="150")
    # возврат оплаты прошлого месяца — вчера; возврат сегодняшней — сегодня
    await refund_event(session, client, YESTERDAY, "300.00")
    await refund_event(session, client, TODAY_EARLY, "149.50")
    await refund_event(session, client, LAST_MONTH, "10.00")

    result = await stats.collect()

    assert result.sales_today == SalesTotal(
        count=1, amount=Decimal("0.50"), refunds=RefundsTotal(1, Decimal("149.50"))
    )
    assert result.sales_month == SalesTotal(
        count=1, amount=Decimal("-299.50"), refunds=RefundsTotal(2, Decimal("449.50"))
    )
