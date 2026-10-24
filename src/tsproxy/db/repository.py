"""Атомарные операции «ровно один раз», которые нельзя доверить проверке в Python.

Каждая — один `UPDATE … WHERE <ещё не сделано>`: если два запроса пришли одновременно,
строку изменит только первый, второй получит `False`.
"""

from datetime import datetime

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.models import Client, NodeOutage


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


def affected_rows(result: object) -> int:
    """Сколько строк изменил `UPDATE` или `DELETE`."""
    if not isinstance(result, CursorResult):
        raise TypeError(f"Ожидался результат UPDATE/DELETE, получено {type(result).__name__}")
    return result.rowcount
