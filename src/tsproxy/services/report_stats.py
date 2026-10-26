"""Цифры для ежедневного отчёта (задача 22): что было за сутки и что висит сейчас.

Минимальный набор запросов: полноценная статистика админки — задача 19. Когда она
появится, отчёт можно перевести на её запросы.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import OrderStatus, TicketStatus
from tsproxy.db.models import Client, Event, Order, SupportTicket
from tsproxy.domain.monitoring import ReportDay
from tsproxy.services.events import EventKind

# Оплачены, а доступ не выдан: ждут повтора выдачи или администратора
UNFINISHED_ORDER_STATUSES = (
    OrderStatus.PAID,
    OrderStatus.ACCESS_FAILED,
    OrderStatus.MANUAL_REVIEW,
)
ZERO_RUBLES = Decimal("0.00")


@dataclass(frozen=True)
class DayStats:
    new_clients: int  # впервые написали боту за сутки
    trials: int  # выдано пробных (включая повторные после сброса администратором)
    payments: int  # оплаченных заказов
    revenue: Decimal  # сколько оплачено, ₽
    unfinished_orders: int  # сейчас: оплачены, а доступ не выдан
    open_tickets: int  # сейчас: открытых обращений


async def day_stats(session_factory: async_sessionmaker[AsyncSession], day: ReportDay) -> DayStats:
    """Цифры за сутки `day` (по Москве) и текущие «хвосты»."""
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
        # суммируем в Python: SQLite складывает NUMERIC как float и теряет копейки
        paid_amounts = list(
            await session.scalars(
                select(Order.paid_amount).where(Order.paid_at >= day.start, Order.paid_at < day.end)
            )
        )
        unfinished = await session.scalar(
            select(func.count(Order.id)).where(Order.status.in_(UNFINISHED_ORDER_STATUSES))
        )
        open_tickets = await session.scalar(
            select(func.count(SupportTicket.id)).where(SupportTicket.status == TicketStatus.OPEN)
        )
    return DayStats(
        new_clients=new_clients or 0,
        trials=trials or 0,
        payments=len(paid_amounts),
        revenue=sum((a for a in paid_amounts if a is not None), ZERO_RUBLES),
        unfinished_orders=unfinished or 0,
        open_tickets=open_tickets or 0,
    )
