"""Проверка «бот жив» для `/health` (задача 18 в `vpn-bot-rework`, задача 22).

Три части: база отвечает, Remnawave отвечает, ни одна фоновая задача не зависла.
Наружу (UptimeRobot) уходит только «да/нет», подробности — в лог сервера.

Защита от частых запросов: результат помнится `cache_for` (30 секунд), а одновременные
запросы ждут одну общую проверку. Так поток запросов к `/health` не превращается в поток
запросов к панели и базе.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.integrations.remnawave_types import RemnawaveClient, RemnawaveError
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.job_health import JobHealth

logger = logging.getLogger(__name__)

HEALTH_CACHE_FOR = timedelta(seconds=30)
# Каждая часть проверки — не дольше 5 секунд: UptimeRobot ждёт ответа 30 секунд
HEALTH_PART_TIMEOUT = timedelta(seconds=5)


@dataclass(frozen=True)
class _Cached:
    checked_at: datetime
    healthy: bool


class HealthCheck:
    """База, Remnawave и свежесть фоновых задач — одним ответом «ok» или «проблема»."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        jobs: JobHealth,
        clock: Clock,
        *,
        cache_for: timedelta = HEALTH_CACHE_FOR,
        part_timeout: timedelta = HEALTH_PART_TIMEOUT,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._jobs = jobs
        self._clock = clock
        self._cache_for = cache_for
        self._timeout = part_timeout.total_seconds()
        self._lock = asyncio.Lock()
        self._cached: _Cached | None = None

    async def is_healthy(self) -> bool:
        async with self._lock:
            now = utc_now(self._clock)
            cached = self._cached
            if cached is not None and now - cached.checked_at < self._cache_for:
                return cached.healthy
            healthy = await self._check()
            self._cached = _Cached(checked_at=now, healthy=healthy)
            return healthy

    async def _check(self) -> bool:
        database = await self._database_ok()
        panel = await self._panel_ok()
        jobs = self._jobs_ok()
        return database and panel and jobs

    async def _database_ok(self) -> bool:
        try:
            async with asyncio.timeout(self._timeout), self._session_factory() as session:
                await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            logger.warning("/health: база не отвечает: %r", error)
            return False
        return True

    async def _panel_ok(self) -> bool:
        try:
            async with asyncio.timeout(self._timeout):
                await self._remnawave.check_panel_version()
        except (RemnawaveError, TimeoutError) as error:
            logger.warning("/health: Remnawave не отвечает: %r", error)
            return False
        return True

    def _jobs_ok(self) -> bool:
        stale = [state.name for state in self._jobs.stale()]
        if stale:
            logger.warning("/health: зависли фоновые задачи: %s", ", ".join(stale))
            return False
        return True
