"""Объявление клиентам в приложении (задача 35): `/announce` в группе администраторов.

Текст уходит в заголовок `announce` общих настроек подписки Remnawave. Happ и v2RayTun
показывают его, когда обновят подписку, — даже если бот и Telegram недоступны. Настройки
одни на всю панель: объявление видят все клиенты, и нынешние, заведённые вручную, тоже.

Каждое изменение — событие `ADMIN_ACTION` в журнале: кто поставил или снял и какой текст.
Сначала меняется панель, потом пишется журнал: не получилось в панели — записи нет.
"""

import asyncio
import logging
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.domain.announcement import (
    AnnouncementProblem,
    announcement_problem,
    decode_announcement,
    encode_announcement,
)
from tsproxy.integrations.remnawave_types import RemnawaveClient
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)


class AnnouncementActionKind(StrEnum):
    """Поле `action` в подробностях события `ADMIN_ACTION`."""

    SET = "announce_set"  # объявление поставлено (или заменено)
    CLEARED = "announce_cleared"  # объявление снято


class AnnouncementService:
    """Читает, ставит и снимает объявление в приложении через настройки подписки панели."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        events: EventLog,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._events = events
        # Панель меняется «прочитать → поменять → записать». Два администратора сразу
        # не должны затереть друг друга
        self._lock = asyncio.Lock()

    async def current(self) -> str | None:
        """Текст объявления, которое сейчас стоит. `None` — объявления нет."""
        value = await self._remnawave.get_announce()
        return None if value is None else decode_announcement(value)

    async def publish(self, admin_id: int, text: str) -> AnnouncementProblem | None:
        """Ставит объявление (заменяет прежнее). `None` — поставлено; иначе — почему нет."""
        problem = announcement_problem(text)
        if problem is not None:
            return problem
        clean = text.strip()
        async with self._lock:
            previous = await self._remnawave.set_announce(encode_announcement(clean))
            await self._record(
                admin_id,
                AnnouncementActionKind.SET,
                text=clean,
                previous=None if previous is None else decode_announcement(previous),
            )
        logger.warning("Объявление в приложении поставил администратор %s", admin_id)
        return None

    async def withdraw(self, admin_id: int) -> str | None:
        """Снимает объявление. Возвращает снятый текст; `None` — объявления и не было."""
        async with self._lock:
            previous = await self._remnawave.clear_announce()
            if previous is None:
                return None
            text = decode_announcement(previous)
            await self._record(admin_id, AnnouncementActionKind.CLEARED, text=text)
        logger.warning("Объявление в приложении снял администратор %s", admin_id)
        return text

    async def _record(
        self, admin_id: int, action: AnnouncementActionKind, **details: object
    ) -> None:
        async with self._session_factory() as session:
            await self._events.record(
                session,
                EventKind.ADMIN_ACTION,
                admin_id=admin_id,
                details={"action": action.value, **details},
            )
            await session.commit()
