"""Защита поддержки: предел сообщений клиента и очередь сообщений одного клиента.

- **Флуд:** больше 10 сообщений в минуту — просим подождать (решение владельца, аудит §4.5).
  Обычные сообщения клиента не проходят общий предел частоты (`bot.throttling`): альбом из
  10 фото — это 10 сообщений сразу, и все они должны дойти до поддержки.
- **Очередь:** сообщения одного клиента обрабатываются по одному. Иначе альбом (несколько
  сообщений одновременно) открыл бы несколько обращений и тем. Порядок частей альбома
  в теме не гарантирован: Telegram присылает их отдельными апдейтами, и они могут прийти
  в любом порядке.
  Бот работает одним процессом, поэтому замка в памяти достаточно.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from tsproxy.bot.throttling import MonotonicClock, RateDecision, RateLimiter
from tsproxy.domain.support import SUPPORT_MESSAGES_PER_MINUTE


def support_rate_limiter(clock: MonotonicClock = time.monotonic) -> RateLimiter:
    """10 сообщений подряд, дальше — не чаще 10 в минуту."""
    return RateLimiter(
        burst=SUPPORT_MESSAGES_PER_MINUTE, per_minute=SUPPORT_MESSAGES_PER_MINUTE, clock=clock
    )


class KeyedLock:
    """Замок на каждый ключ (Telegram ID). Свободные замки сразу забываются."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._holders: dict[int, int] = {}  # сколько задач держат замок или ждут его

    @asynccontextmanager
    async def __call__(self, key: int) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._holders[key] = self._holders.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._holders[key] -= 1
            if self._holders[key] == 0:
                del self._holders[key]
                del self._locks[key]

    def __len__(self) -> int:
        return len(self._locks)


class SupportGuard:
    """Предел частоты и очередь сообщений в поддержку — на каждого клиента."""

    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter
        self.lock = KeyedLock()

    def check_flood(self, telegram_id: int) -> RateDecision:
        return self._limiter.check(telegram_id)
