"""Чистка удаляет только то, у чего вышел срок хранения."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.enums import MessageAuthor, NotificationKind
from tsproxy.db.models import Client, Notification, SupportTicket, TicketMessage
from tsproxy.db.retention import (
    NOTIFICATIONS_RETENTION,
    TICKET_MESSAGES_RETENTION,
    delete_expired,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
MARGIN = timedelta(days=1)


async def test_delete_expired_removes_only_old_rows(session: AsyncSession, client: Client) -> None:
    old_expiry, fresh_expiry = NOW - timedelta(days=100), NOW
    session.add_all(
        [
            Notification(
                client_id=client.id,
                kind=NotificationKind.DAY_BEFORE,
                related_expire_at=old_expiry,
                sent_at=NOW - NOTIFICATIONS_RETENTION - MARGIN,
            ),
            Notification(
                client_id=client.id,
                kind=NotificationKind.DAY_BEFORE,
                related_expire_at=fresh_expiry,
                sent_at=NOW - NOTIFICATIONS_RETENTION + MARGIN,
            ),
        ]
    )
    ticket = SupportTicket(client_id=client.id, opened_at=NOW - TICKET_MESSAGES_RETENTION * 2)
    session.add(ticket)
    await session.flush()
    session.add_all(
        [
            TicketMessage(
                ticket_id=ticket.id,
                author=MessageAuthor.CLIENT,
                text="старое",
                created_at=NOW - TICKET_MESSAGES_RETENTION - MARGIN,
            ),
            TicketMessage(
                ticket_id=ticket.id,
                author=MessageAuthor.SUPPORT,
                text="свежее",
                created_at=NOW - TICKET_MESSAGES_RETENTION + MARGIN,
            ),
        ]
    )
    await session.commit()

    result = await delete_expired(session, NOW)
    await session.commit()

    assert (result.notifications, result.ticket_messages) == (1, 1)
    notifications = (await session.execute(select(Notification.related_expire_at))).scalars()
    assert list(notifications) == [fresh_expiry]
    texts = (await session.execute(select(TicketMessage.text))).scalars()
    assert list(texts) == ["свежее"]
    # само обращение и клиент не удаляются
    assert (await session.execute(select(SupportTicket))).scalar_one().id == ticket.id
