"""Чистка старых данных раз в сутки в 05:00 МСК — после бэкапа базы бота в 04:30.

Что и сколько хранится — `db/retention.py`. Сбой летит дальше — его увидит сторож
фоновых задач, следующая попытка через сутки.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.retention import delete_expired
from tsproxy.services.clock import Clock, utc_now

logger = logging.getLogger(__name__)


class DataCleanup:
    """Удаляет уведомления и переписку поддержки, у которых вышел срок хранения."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def run(self) -> None:
        async with self._session_factory() as session:
            result = await delete_expired(session, utc_now(self._clock))
            await session.commit()
        logger.info(
            "Чистка старых данных: уведомлений %d, сообщений поддержки %d",
            result.notifications,
            result.ticket_messages,
        )
