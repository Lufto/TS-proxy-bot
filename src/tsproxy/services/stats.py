"""Статистика для администраторов (задача 19, спецификация «Админка» п. 4).

Что считаем и откуда:
- продажи за день и за месяц (по Москве) — оплаченные заказы по `paid_at` (когда бот
  засчитал оплату), сумма — сколько заплачено на самом деле (`paid_amount`) за вычетом
  возвратов за тот же период. Возврат считается по времени, когда платёжка вернула деньги
  (событие `REFUND_COMPLETED`, задача 21), а не по дате самой оплаты: оплату прошлого
  месяца, возвращённую сегодня, вычитаем из сегодняшних. Возвраты — отдельной строкой;
- живые подписки (оплаченные, льготный период, пробные) — по аккаунтам бота из Remnawave:
  срок знает только панель. `list_accounts` отдаёт только аккаунты бота, 28 нынешних
  клиентов панели сюда не попадают. Панель не ответила — эта строка неизвестна, остальное
  из базы всё равно показываем;
- конверсия пробного — клиенты, которым пробный доставлен (есть событие «пробный выдан»),
  и сколько из них заплатили после первого пробного. За всё время;
- поддержка — открытые сейчас (по `status`: `closed_at` остаётся при переоткрытии),
  ждущие первого ответа, открытые за месяц и время первого ответа по ним;
- клиенты бота всего и сколько заблокировали бота.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import TicketStatus
from tsproxy.db.models import Client, Event, Order, SupportTicket
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import UNLIMITED_TRAFFIC_BYTES
from tsproxy.domain.stats import (
    ResponseTimes,
    SubscriptionCounts,
    conversion_percent,
    moscow_day_start,
    moscow_month_start,
    response_times,
    tally_phases,
)
from tsproxy.domain.subscription import subscription_phase
from tsproxy.integrations.remnawave_types import (
    AccountStatus,
    ProxyAccount,
    RemnawaveClient,
    RemnawaveUnavailable,
)
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefundsTotal:
    """Возвраты за период: сколько и на какую сумму вернула платёжка."""

    count: int
    amount: Decimal


NO_REFUNDS = RefundsTotal(0, Decimal(0))


@dataclass(frozen=True)
class SalesTotal:
    """Оплаты за период. `amount` — поступило за вычетом `refunds` (может быть меньше нуля)."""

    count: int
    amount: Decimal
    refunds: RefundsTotal = NO_REFUNDS


@dataclass(frozen=True)
class TrialStats:
    """Сколько клиентов получили пробный, сколько потом заплатили и доля в процентах."""

    trials: int
    converted: int
    percent: int | None  # None — пробных ещё не было


@dataclass(frozen=True)
class SupportStats:
    open_now: int
    waiting_first_reply: int  # открыты, а первого ответа ещё нет
    opened_month: int
    response: ResponseTimes | None  # по обращениям за месяц; None — ответов не было


@dataclass(frozen=True)
class ClientTotals:
    total: int
    blocked: int  # заблокировали бота: им не уходят уведомления и рассылки


@dataclass(frozen=True)
class Stats:
    collected_at: datetime
    sales_today: SalesTotal
    sales_month: SalesTotal
    subscriptions: SubscriptionCounts | None  # None — панель не ответила
    trials: TrialStats
    support: SupportStats
    clients: ClientTotals


class StatsService:
    """Собирает статистику из базы бота и списка аккаунтов бота в Remnawave. Ничего не меняет."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._clock = clock

    async def collect(self) -> Stats:
        now = utc_now(self._clock)
        month_start = moscow_month_start(now)
        async with self._session_factory() as session:
            sales_today = await _sales(session, moscow_day_start(now), now)
            sales_month = await _sales(session, month_start, now)
            trials = await _trials(session)
            support = await _support(session, month_start)
            clients = await _clients(session)
            subscriptions = await self._subscriptions(session, now)
        return Stats(now, sales_today, sales_month, subscriptions, trials, support, clients)

    async def _subscriptions(
        self, session: AsyncSession, now: datetime
    ) -> SubscriptionCounts | None:
        try:
            accounts = await self._remnawave.list_accounts()
        except RemnawaveUnavailable as error:
            logger.warning("Статистика: панель не ответила, подписки не посчитаны: %s", error)
            return None
        ids = [account.telegram_id for account in accounts if account.telegram_id is not None]
        rows = await session.scalars(select(Client).where(Client.telegram_id.in_(ids)))
        by_telegram_id = {client.telegram_id: client for client in rows}
        return tally_phases(
            subscription_phase(_snapshot(account, by_telegram_id.get(account.telegram_id)), now)
            for account in accounts
        )


def _snapshot(account: ProxyAccount, client: Client | None) -> ClientSnapshot:
    """Состояние для правил сроков — как у `services.grace.client_snapshot`.

    Аккаунта без строки клиента быть не должно (аккаунт создаёт бот для клиента из базы);
    если такой найдётся — пробным считаем по лимиту трафика: он стоит только у пробных.
    """
    limited = account.traffic_limit_bytes != UNLIMITED_TRAFFIC_BYTES
    trial_granted = client is None or client.trial_granted_at is not None
    return ClientSnapshot(
        expire_at=account.expire_at,
        on_trial=trial_granted and limited,
        grace_started_at=None if client is None else client.grace_started_at,
        access_disabled=account.status is AccountStatus.DISABLED,
    )


async def _sales(session: AsyncSession, since: datetime, until: datetime) -> SalesTotal:
    rows = await session.execute(
        select(Order.paid_amount, Order.price).where(
            Order.paid_at.is_not(None), Order.paid_at >= since, Order.paid_at <= until
        )
    )
    amounts = [paid if paid is not None else price for paid, price in rows]
    refunds = await _refunds(session, since, until)
    return SalesTotal(
        count=len(amounts), amount=sum(amounts, Decimal(0)) - refunds.amount, refunds=refunds
    )


async def _refunds(session: AsyncSession, since: datetime, until: datetime) -> RefundsTotal:
    """Деньги, которые платёжка вернула за период (по событию «платёжка вернула деньги»)."""
    details = await session.scalars(
        select(Event.details).where(
            Event.kind == EventKind.REFUND_COMPLETED.value,
            Event.occurred_at >= since,
            Event.occurred_at <= until,
        )
    )
    amounts = [Decimal(item["amount"]) for item in details if item is not None]
    return RefundsTotal(count=len(amounts), amount=sum(amounts, Decimal(0)))


async def _trials(session: AsyncSession) -> TrialStats:
    first_trial = (
        select(Event.client_id, func.min(Event.occurred_at).label("first_at"))
        .where(Event.kind == EventKind.TRIAL_GRANTED.value, Event.client_id.is_not(None))
        .group_by(Event.client_id)
        .subquery()
    )
    trials = await session.scalar(select(func.count()).select_from(first_trial)) or 0
    converted = (
        await session.scalar(
            select(func.count(func.distinct(first_trial.c.client_id))).join(
                Order,
                (Order.client_id == first_trial.c.client_id)
                & Order.paid_at.is_not(None)
                & (Order.paid_at >= first_trial.c.first_at),
            )
        )
        or 0
    )
    return TrialStats(trials, converted, conversion_percent(trials, converted))


async def _support(session: AsyncSession, month_start: datetime) -> SupportStats:
    is_open = SupportTicket.status == TicketStatus.OPEN
    open_now = await session.scalar(select(func.count()).where(is_open)) or 0
    waiting = (
        await session.scalar(
            select(func.count()).where(is_open, SupportTicket.first_response_at.is_(None))
        )
        or 0
    )
    month = list(
        await session.execute(
            select(SupportTicket.opened_at, SupportTicket.first_response_at).where(
                SupportTicket.opened_at >= month_start
            )
        )
    )
    answered = [replied - opened for opened, replied in month if replied is not None]
    return SupportStats(open_now, waiting, len(month), response_times(answered))


async def _clients(session: AsyncSession) -> ClientTotals:
    total = await session.scalar(select(func.count()).select_from(Client)) or 0
    blocked = (
        await session.scalar(select(func.count()).where(Client.bot_blocked_at.is_not(None))) or 0
    )
    return ClientTotals(total, blocked)
