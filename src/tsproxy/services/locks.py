"""Блокировка «по одному действию на клиента» внутри одного процесса бота.

Два нажатия «Купить» одного клиента приходят почти одновременно — второе должно подождать
первое и увидеть его результат, а не создать второй платёж. Бот работает одним процессом
с одной базой (аудит §7.1), поэтому хватает `asyncio.Lock` в памяти.
"""

import asyncio
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager


class KeyedLocks:
    """`asyncio.Lock` на каждый ключ. Ненужные замки забываются, память не растёт."""

    def __init__(self) -> None:
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._waiting: dict[Hashable, int] = {}

    @asynccontextmanager
    async def hold(self, key: Hashable) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._waiting[key] -= 1
            if self._waiting[key] == 0:
                del self._waiting[key]
                del self._locks[key]

    def held_keys(self) -> set[Hashable]:
        """Ключи, по которым кто-то держит замок или ждёт его."""
        return set(self._locks)
