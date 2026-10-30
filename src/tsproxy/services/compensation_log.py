"""Компенсации за сбой в журнале событий: поля событий и продление льготного (задача 20).

Отдельный модуль, чтобы обработчик льготного (`services/grace.py`) и компенсация
(`services/compensation.py`) читали одно и то же, не завися друг от друга.

Продление льготного периода — без новой колонки (решение владельца 2026-10-26): это сумма
событий `COMPENSATION_GRANTED` вида EXTEND_GRACE, записанных с начала текущего льготного.
Событие пишется до того, как срок поставлен в панели, поэтому даже после сбоя посреди
начисления обработчик льготного сам доведёт срок в панели до нового конца.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.models import Event
from tsproxy.domain.client_state import NO_GRACE_EXTENSION
from tsproxy.domain.compensation import CompensationAction
from tsproxy.services.events import EventKind

# Поля подробностей события начисления клиенту
ACTION = "action"  # `CompensationAction`
AMOUNT_SECONDS = "amount_seconds"


async def grace_extension(
    session: AsyncSession, client_id: int, grace_started_at: datetime | None
) -> timedelta:
    """На сколько компенсации продлили льготный период, начатый в `grace_started_at`.

    Следующий льготный начнётся позже этих событий — старое продление в него не попадёт.
    """
    if grace_started_at is None:
        return NO_GRACE_EXTENSION
    details = await session.scalars(
        select(Event.details).where(
            Event.kind == EventKind.COMPENSATION_GRANTED.value,
            Event.client_id == client_id,
            Event.occurred_at >= grace_started_at,
        )
    )
    return sum(
        (
            timedelta(seconds=item[AMOUNT_SECONDS])
            for item in details
            if item is not None and item.get(ACTION) == CompensationAction.EXTEND_GRACE
        ),
        NO_GRACE_EXTENSION,
    )
