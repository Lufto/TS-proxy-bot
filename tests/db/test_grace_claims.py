"""Атомарные отметки льготного периода (задача 11): «льготный начался» и «сообщение отправлено»."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.enums import NotificationKind
from tsproxy.db.models import Client, Notification
from tsproxy.db.repository import claim_grace, claim_notification, release_notification

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


async def test_grace_claimed_only_once(session: AsyncSession, client: Client) -> None:
    assert await claim_grace(session, client.id, NOW) is True
    # повторный вебхук не сдвигает начало — значит, и 6 дней не будет
    assert await claim_grace(session, client.id, NOW + timedelta(hours=1)) is False
    await session.commit()

    await session.refresh(client)
    assert client.grace_started_at == NOW


async def test_notification_claimed_only_once(session: AsyncSession, client: Client) -> None:
    kind = NotificationKind.GRACE_REMINDER
    assert await claim_notification(session, client.id, kind, NOW, NOW) is True
    assert await claim_notification(session, client.id, kind, NOW, NOW) is False
    await session.commit()

    rows = (await session.scalars(select(Notification))).all()
    assert [(r.kind, r.related_expire_at, r.sent_at) for r in rows] == [(kind, NOW, NOW)]


async def test_other_day_or_kind_is_a_different_notification(
    session: AsyncSession, client: Client
) -> None:
    kind = NotificationKind.GRACE_REMINDER
    assert await claim_notification(session, client.id, kind, NOW, NOW)
    assert await claim_notification(session, client.id, kind, NOW + timedelta(days=1), NOW)
    assert await claim_notification(session, client.id, NotificationKind.GRACE_LAST_DAY, NOW, NOW)


async def test_released_notification_can_be_claimed_again(
    session: AsyncSession, client: Client
) -> None:
    # сообщение не ушло (сбой Telegram) — отметку снимаем, следующая проверка повторит
    kind = NotificationKind.GRACE
    await claim_notification(session, client.id, kind, NOW, NOW)
    await release_notification(session, client.id, kind, NOW)

    assert await claim_notification(session, client.id, kind, NOW, NOW) is True
