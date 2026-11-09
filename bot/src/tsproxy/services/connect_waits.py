"""Клиент нажал «🚀 Подключиться», а панель не ответила: бот сам пришлёт ссылку, когда панель
оживёт (владелец, 2026-11-10).

Ожидание — событие `CONNECT_WAITING` в журнале, поэтому переживает перезапуск бота. Клиент
получил ссылку — событие `CONNECT_DELIVERED`: бот прислал её сам или клиент снова нажал
«Подключиться», и панель ответила (`details["by"]`). Ждём не дольше суток: позже сообщение
со ссылкой уже не к месту.
"""

import logging
from datetime import datetime
from enum import StrEnum

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute, aliased
from sqlalchemy.sql.expression import Exists

from tsproxy.db.models import Client, Event
from tsproxy.domain.constants import CONNECT_WAIT_WINDOW
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)


class DeliveredBy(StrEnum):
    """Кто закрыл ожидание — поле `by` события `CONNECT_DELIVERED`."""

    BOT = "bot"  # фоновая задача прислала ссылку
    CLIENT = "client"  # клиент сам снова открыл «Подключиться»


class ConnectWaits:
    """Кто ждёт ссылку после сбоя панели."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        events: EventLog,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._events = events
        self._clock = clock

    async def wait(self, client_id: int) -> None:
        """Панель не ответила клиенту — запомнить, что он ждёт ссылку."""
        async with self._session_factory() as session:
            await self._events.record(session, EventKind.CONNECT_WAITING, client_id=client_id)
            await session.commit()
        logger.info("Клиент %s ждёт ссылку: панель не ответила", client_id)

    async def delivered(self, client_id: int, by: DeliveredBy) -> None:
        """Клиент получил ссылку. Записывается, только если он её ждал."""
        async with self._session_factory() as session:
            if not await self._is_waiting(session, client_id):
                return
            await self._events.record(
                session, EventKind.CONNECT_DELIVERED, client_id=client_id, details={"by": by.value}
            )
            await session.commit()

    async def waiting(self) -> list[int]:
        """Telegram ID клиентов, которые ждут ссылку не дольше суток."""
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(Client.telegram_id).where(_waiting(Client.id, utc_now(self._clock)))
            )
            return list(rows)

    async def _is_waiting(self, session: AsyncSession, client_id: int) -> bool:
        return bool(await session.scalar(select(_waiting(client_id, utc_now(self._clock)))))


def _waiting(client_id: int | InstrumentedAttribute[int], now: datetime) -> Exists:
    """Есть свежее «ждёт ссылку», после которого ссылку ещё не получил."""
    wait = aliased(Event)
    done = aliased(Event)
    return exists().where(
        wait.client_id == client_id,
        wait.kind == EventKind.CONNECT_WAITING.value,
        wait.occurred_at > now - CONNECT_WAIT_WINDOW,
        ~exists().where(
            done.client_id == client_id,
            done.kind == EventKind.CONNECT_DELIVERED.value,
            done.occurred_at >= wait.occurred_at,
        ),
    )
