"""Чистка старых данных: уведомления — 90 дней, переписка поддержки — 3 года.

Клиенты, заказы, журнал событий, возвраты и сбои хранятся бессрочно.
Запуск по расписанию — позже, в фоновых задачах.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Delete, delete
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.models import Notification, TicketMessage
from tsproxy.db.repository import affected_rows

NOTIFICATIONS_RETENTION = timedelta(days=90)
DAYS_PER_YEAR = 365
TICKET_MESSAGES_RETENTION_YEARS = 3
TICKET_MESSAGES_RETENTION = timedelta(days=DAYS_PER_YEAR * TICKET_MESSAGES_RETENTION_YEARS)


@dataclass(frozen=True)
class CleanupResult:
    """Сколько строк удалено."""

    notifications: int
    ticket_messages: int


def old_notifications_query(now: datetime) -> Delete:
    """Уведомления, отправленные раньше чем 90 дней назад."""
    return delete(Notification).where(Notification.sent_at < now - NOTIFICATIONS_RETENTION)


def old_ticket_messages_query(now: datetime) -> Delete:
    """Сообщения переписки старше 3 лет."""
    return delete(TicketMessage).where(TicketMessage.created_at < now - TICKET_MESSAGES_RETENTION)


async def delete_expired(session: AsyncSession, now: datetime) -> CleanupResult:
    """Удалить всё, у чего вышел срок хранения. Коммит — за вызывающим кодом."""
    notifications = await session.execute(old_notifications_query(now))
    ticket_messages = await session.execute(old_ticket_messages_query(now))
    return CleanupResult(
        notifications=affected_rows(notifications),
        ticket_messages=affected_rows(ticket_messages),
    )
