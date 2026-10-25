"""Атомарные операции «ровно один раз», которые нельзя доверить проверке в Python.

Каждая — один `UPDATE … WHERE <ещё не сделано>`: если два запроса пришли одновременно,
строку изменит только первый, второй получит `False`.
"""

from datetime import datetime

from sqlalchemy import CursorResult, delete, exists, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.enums import NotificationKind
from tsproxy.db.models import Client, NodeOutage, Notification


async def claim_trial(session: AsyncSession, client_id: int, now: datetime) -> bool:
    """Отметить выдачу пробного периода. `False` — пробный уже был выдан."""
    result = await session.execute(
        update(Client)
        .where(Client.id == client_id, Client.trial_granted_at.is_(None))
        .values(trial_granted_at=now)
    )
    return affected_rows(result) == 1


async def reclaim_trial(
    session: AsyncSession, client_id: int, claimed_at: datetime, now: datetime
) -> bool:
    """Перехватить зависшую выдачу пробного (отметка есть, аккаунта нет).

    `False` — отметку уже перехватил параллельный запрос.
    """
    result = await session.execute(
        update(Client)
        .where(Client.id == client_id, Client.trial_granted_at == claimed_at)
        .values(trial_granted_at=now)
    )
    return affected_rows(result) == 1


async def claim_outage_compensation(
    session: AsyncSession, outage_id: int, admin_id: int, now: datetime
) -> bool:
    """Отметить компенсацию за сбой. `False` — за этот сбой уже компенсировали."""
    result = await session.execute(
        update(NodeOutage)
        .where(NodeOutage.id == outage_id, NodeOutage.compensated_at.is_(None))
        .values(compensated_at=now, compensated_by_admin_id=admin_id)
    )
    return affected_rows(result) == 1


async def claim_grace(session: AsyncSession, client_id: int, started_at: datetime) -> bool:
    """Отметить «льготный начался» (задача 11). `False` — отметка уже стоит:
    повторное событие об окончании не даёт ещё 3 дня."""
    result = await session.execute(
        update(Client)
        .where(Client.id == client_id, Client.grace_started_at.is_(None))
        .values(grace_started_at=started_at)
    )
    return affected_rows(result) == 1


async def claim_notification(
    session: AsyncSession,
    client_id: int,
    kind: NotificationKind,
    related_expire_at: datetime,
    now: datetime,
) -> bool:
    """Записать уведомление до отправки. `False` — такое уже отправлено.

    `INSERT … SELECT … WHERE NOT EXISTS`: на дубль нет ни строки, ни ошибки уникальности,
    поэтому остальная транзакция не откатывается.
    """
    table = Notification.__table__.c
    already = exists().where(
        Notification.client_id == client_id,
        Notification.kind == kind,
        Notification.related_expire_at == related_expire_at,
    )
    values = select(
        literal(client_id),
        literal(kind, type_=table.kind.type),
        literal(related_expire_at, type_=table.related_expire_at.type),
        literal(now, type_=table.sent_at.type),
    ).where(~already)
    result = await session.execute(
        insert(Notification).from_select(
            ["client_id", "kind", "related_expire_at", "sent_at"], values
        )
    )
    return affected_rows(result) == 1


async def release_notification(
    session: AsyncSession, client_id: int, kind: NotificationKind, related_expire_at: datetime
) -> None:
    """Снять запись уведомления, которое не ушло: следующая проверка пришлёт его снова."""
    await session.execute(
        delete(Notification).where(
            Notification.client_id == client_id,
            Notification.kind == kind,
            Notification.related_expire_at == related_expire_at,
        )
    )


def affected_rows(result: object) -> int:
    """Сколько строк изменил `UPDATE` или `DELETE`."""
    if not isinstance(result, CursorResult):
        raise TypeError(f"Ожидался результат UPDATE/DELETE, получено {type(result).__name__}")
    return result.rowcount
