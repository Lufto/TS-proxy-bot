"""Цифры «за вчера» для ежедневного отчёта (задача 22).

Продажи считаются тем же запросом, что и в `/stats` (задача 19, `services/stats.py`),
текущие цифры (подписки, обращения, клиенты) отчёт берёт из `StatsService.collect`.
Здесь — только то, чего в `/stats` нет: новые клиенты и пробные за сутки и оплаченные
заказы, по которым доступ так и не выдан.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.domain.monitoring import ReportDay
from tsproxy.services.events import EventKind
from tsproxy.services.stats import SalesTotal, sales_between

# Оплачены, а доступ не выдан: ждут повтора выдачи или администратора
UNFINISHED_ORDER_STATUSES = (
    OrderStatus.PAID,
    OrderStatus.ACCESS_FAILED,
    OrderStatus.MANUAL_REVIEW,
)
# `sales_between` включает правую границу, а сутки — полуинтервал [00:00, 24:00)
_JUST_BEFORE = timedelta(microseconds=1)


@dataclass(frozen=True)
class DayStats:
    new_clients: int  # впервые написали боту за сутки
    trials: int  # выдано пробных (включая повторные после сброса администратором)
    sales: SalesTotal  # оплаченные за сутки заказы и сумма, ₽ (звёзды — в `sales.stars`)
    unfinished_orders: int  # сейчас: оплачены, а доступ не выдан


async def day_stats(
    session_factory: async_sessionmaker[AsyncSession], day: ReportDay, stars_rate: Decimal
) -> DayStats:
    """Цифры за сутки `day` (по Москве) и заказы, которые висят сейчас.

    `stars_rate` — курс звезды: оплаты звёздами «примерно в рублях» (задача 33).
    """
    async with session_factory() as session:
        new_clients = await session.scalar(
            select(func.count(Client.id)).where(
                Client.first_seen_at >= day.start, Client.first_seen_at < day.end
            )
        )
        trials = await session.scalar(
            select(func.count(Event.id)).where(
                Event.kind == EventKind.TRIAL_GRANTED.value,
                Event.occurred_at >= day.start,
                Event.occurred_at < day.end,
            )
        )
        sales = await sales_between(session, day.start, day.end - _JUST_BEFORE, stars_rate)
        unfinished = await session.scalar(
            select(func.count(Order.id)).where(Order.status.in_(UNFINISHED_ORDER_STATUSES))
        )
    return DayStats(
        new_clients=new_clients or 0,
        trials=trials or 0,
        sales=sales,
        unfinished_orders=unfinished or 0,
    )
