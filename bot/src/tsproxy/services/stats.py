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

Задача 33: оплаты звёздами Telegram — отдельной строкой. В рублёвые продажи и возвраты они
не входят (рубли заказа у них — расчётные). Звёзды — из события «оплата получена»
(`crediting.STARS_DETAIL`), за вычетом звёзд, возвращённых за тот же период (по времени
возврата, как у рублей); «примерно в рублях» — по нынешнему курсу из `tariffs.yaml`.

Заказы тестового тарифа администраторов (`ADMIN_TEST_TARIFF_ID`, владелец 2026-11-20) нигде
не считаются: ни в продажах, ни в возвратах, ни в звёздах, ни в конверсии пробного.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import TicketStatus
from tsproxy.db.models import Client, Event, Order, SupportTicket
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import ADMIN_TEST_TARIFF_ID, UNLIMITED_TRAFFIC_BYTES
from tsproxy.domain.grace import observed_grace_extension
from tsproxy.domain.stars import stars_in_rubles
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
from tsproxy.integrations.telegram_stars import STARS_PROVIDER
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.crediting import paid_stars
from tsproxy.services.events import EventKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefundsTotal:
    """Возвраты за период: сколько и на какую сумму вернула платёжка."""

    count: int
    amount: Decimal


NO_REFUNDS = RefundsTotal(0, Decimal(0))


@dataclass(frozen=True)
class StarsSales:
    """Оплаты звёздами Telegram за период (задача 33). `stars` — за вычетом возвращённых
    за тот же период (может быть меньше нуля), `rubles` — они же примерно в рублях по курсу."""

    count: int
    stars: int
    refunds: int  # сколько оплат звёздами возвращено за период
    rubles: Decimal


NO_STARS = StarsSales(0, 0, 0, Decimal(0))


@dataclass(frozen=True)
class SalesTotal:
    """Оплаты рублями за период. `amount` — поступило за вычетом `refunds` (может быть меньше
    нуля). Оплаты звёздами — отдельно, в `stars` (задача 33)."""

    count: int
    amount: Decimal
    refunds: RefundsTotal = NO_REFUNDS
    stars: StarsSales = NO_STARS


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
    stars_rate: Decimal  # курс звезды в рублях, по которому посчитаны `stars.rubles`


class StatsService:
    """Собирает статистику из базы бота и списка аккаунтов бота в Remnawave. Ничего не меняет."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        clock: Clock,
        stars_rate: Decimal,  # курс звезды в рублях (`stars_rub_rate` из tariffs.yaml)
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._clock = clock
        self._stars_rate = stars_rate

    async def collect(self) -> Stats:
        now = utc_now(self._clock)
        month_start = moscow_month_start(now)
        async with self._session_factory() as session:
            sales_today = await _sales(session, moscow_day_start(now), now, self._stars_rate)
            sales_month = await _sales(session, month_start, now, self._stars_rate)
            trials = await _trials(session)
            support = await _support(session, month_start)
            clients = await _clients(session)
            subscriptions = await self._subscriptions(session, now)
        return Stats(
            now,
            sales_today,
            sales_month,
            subscriptions,
            trials,
            support,
            clients,
            self._stars_rate,
        )

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
    grace_started_at = None if client is None else client.grace_started_at
    return ClientSnapshot(
        expire_at=account.expire_at,
        on_trial=trial_granted and limited,
        grace_started_at=grace_started_at,
        access_disabled=account.status is AccountStatus.DISABLED,
        # компенсация за сбой продлевает льготный (задача 20) — видно по сроку в панели
        grace_extension=observed_grace_extension(grace_started_at, account.expire_at),
    )


async def sales_between(
    session: AsyncSession, since: datetime, until: datetime, stars_rate: Decimal
) -> SalesTotal:
    """Оплаты с `since` по `until` включительно — для ежедневного отчёта (задача 22)."""
    return await _sales(session, since, until, stars_rate)


# Заказ оплачен звёздами Telegram (задача 33) — или нет
_PAID_WITH_STARS = Order.payment_provider == STARS_PROVIDER
_PAID_WITH_RUBLES = or_(Order.payment_provider.is_(None), Order.payment_provider != STARS_PROVIDER)
# Не тестовый заказ администратора. Возврат без заказа (внешнее соединение) — тоже не тестовый
_NOT_ADMIN_TEST = or_(Order.tariff_id.is_(None), Order.tariff_id != ADMIN_TEST_TARIFF_ID)


async def _sales(
    session: AsyncSession, since: datetime, until: datetime, stars_rate: Decimal
) -> SalesTotal:
    rows = await session.execute(
        select(Order.paid_amount, Order.price).where(
            _PAID_WITH_RUBLES,
            _NOT_ADMIN_TEST,
            Order.paid_at.is_not(None),
            Order.paid_at >= since,
            Order.paid_at <= until,
        )
    )
    amounts = [paid if paid is not None else price for paid, price in rows]
    refunds = await _refunds(session, since, until)
    return SalesTotal(
        count=len(amounts),
        amount=sum(amounts, Decimal(0)) - refunds.amount,
        refunds=refunds,
        stars=await _stars_sales(session, since, until, stars_rate),
    )


def _refunded_between(
    since: datetime, until: datetime
) -> Select[tuple[int | None, dict[str, Any] | None]]:
    """Возвраты, которые платёжка сделала за период (событие «платёжка вернула деньги»).

    Событие без заказа считается возвратом рублей.
    """
    return (
        select(Event.order_id, Event.details)
        .outerjoin(Order, Order.id == Event.order_id)
        .where(
            Event.kind == EventKind.REFUND_COMPLETED.value,
            _NOT_ADMIN_TEST,
            Event.occurred_at >= since,
            Event.occurred_at <= until,
        )
    )


async def _refunds(session: AsyncSession, since: datetime, until: datetime) -> RefundsTotal:
    """Рубли, которые платёжка вернула за период. Возвраты звёзд — в `_stars_sales`."""
    rows = await session.execute(_refunded_between(since, until).where(_PAID_WITH_RUBLES))
    amounts = [Decimal(details["amount"]) for _, details in rows if details is not None]
    return RefundsTotal(count=len(amounts), amount=sum(amounts, Decimal(0)))


async def _stars_sales(
    session: AsyncSession, since: datetime, until: datetime, stars_rate: Decimal
) -> StarsSales:
    """Оплаты звёздами за период за вычетом звёзд, возвращённых за тот же период."""
    paid_ids = list(
        await session.scalars(
            select(Order.id).where(
                _PAID_WITH_STARS,
                _NOT_ADMIN_TEST,
                Order.paid_at.is_not(None),
                Order.paid_at >= since,
                Order.paid_at <= until,
            )
        )
    )
    refunded_rows = await session.execute(_refunded_between(since, until).where(_PAID_WITH_STARS))
    refunded_ids = [order_id for order_id, _ in refunded_rows if order_id is not None]
    paid = await paid_stars(session, paid_ids)
    refunded = await paid_stars(session, refunded_ids)
    net = sum(paid.values()) - sum(refunded.get(order_id, 0) for order_id in refunded_ids)
    return StarsSales(len(paid_ids), net, len(refunded_ids), stars_in_rubles(net, stars_rate))


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
                & (Order.paid_at >= first_trial.c.first_at)
                & (Order.tariff_id != ADMIN_TEST_TARIFF_ID),
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
