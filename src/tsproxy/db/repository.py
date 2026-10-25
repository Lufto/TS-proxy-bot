"""Атомарные операции «ровно один раз», которые нельзя доверить проверке в Python.

Каждая — один `UPDATE … WHERE <ещё не сделано>`: если два запроса пришли одновременно,
строку изменит только первый, второй получит `False`.
"""

from datetime import datetime

from sqlalchemy import CursorResult, update
from sqlalchemy.exc import IntegrityError
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


async def claim_trial_extension(
    session: AsyncSession, client_id: int, extensions_used: int
) -> bool:
    """Засчитать очередное продление пробного: счётчик растёт с `extensions_used` на 1.

    `False` — это продление уже засчитал параллельный запрос.
    """
    result = await session.execute(
        update(Client)
        .where(Client.id == client_id, Client.trial_extensions == extensions_used)
        .values(trial_extensions=extensions_used + 1)
    )
    return affected_rows(result) == 1


async def claim_notification(
    session: AsyncSession,
    client_id: int,
    kind: NotificationKind,
    related_expire_at: datetime,
    now: datetime,
) -> bool:
    """Записать уведомление клиенту — до отправки, чтобы оно не ушло дважды.

    Уникальный ключ (клиент, вид, срок) пропускает только одну запись. `False` — такое
    уведомление к этому сроку уже записано; транзакция сессии тогда откатывается целиком,
    вместе со всем, что вызывающий код сделал в ней до этого.
    """
    session.add(
        Notification(
            client_id=client_id, kind=kind, related_expire_at=related_expire_at, sent_at=now
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return False
    return True


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


def affected_rows(result: object) -> int:
    """Сколько строк изменил `UPDATE` или `DELETE`."""
    if not isinstance(result, CursorResult):
        raise TypeError(f"Ожидался результат UPDATE/DELETE, получено {type(result).__name__}")
    return result.rowcount
