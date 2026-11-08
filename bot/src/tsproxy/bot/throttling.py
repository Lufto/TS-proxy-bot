"""Ограничение частоты нажатий и сообщений на одного клиента (аудит §4.5).

Простое «ведро с жетонами» в памяти: каждое действие забирает жетон, жетоны понемногу
возвращаются. Пока жетоны есть, клиент может нажать несколько кнопок подряд; при флуде
лишние действия не обрабатываются. После перезапуска бота счётчики обнуляются — это нормально.
"""

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject

from tsproxy.content.loader import Texts

# Аудит §4.5: не больше ~1 действия в секунду и 30 в минуту
RATE_LIMIT_BURST = 5  # столько действий подряд можно сделать без паузы
RATE_LIMIT_PER_MINUTE = 30  # дальше — не чаще, чем столько в минуту
SECONDS_PER_MINUTE = 60
# Чтобы память не росла бесконечно: при стольких клиентах забываем тех, кто давно молчит
MAX_TRACKED_CLIENTS = 10_000

type MonotonicClock = Callable[[], float]
# Команда бота: `/` и латинские буквы, цифры, `_`; после — `@имя_бота`, пробел или конец
COMMAND_RE = re.compile(r"/[A-Za-z0-9_]+(?:@\w+)?(?:\s|$)")


class RateDecision(StrEnum):
    ALLOW = "allow"
    DENY_FIRST = "deny_first"  # первый отказ подряд — сказать клиенту «подождите»
    DENY = "deny"  # повторный отказ — молчим, чтобы не флудить в ответ


@dataclass
class _Bucket:
    tokens: float
    updated_at: float
    warned: bool = False


class RateLimiter:
    """Ведро с жетонами на каждый ключ (Telegram ID)."""

    def __init__(
        self,
        *,
        burst: int = RATE_LIMIT_BURST,
        per_minute: int = RATE_LIMIT_PER_MINUTE,
        clock: MonotonicClock = time.monotonic,
        max_tracked: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        if burst <= 0 or per_minute <= 0:
            raise ValueError("Лимит частоты должен быть больше нуля")
        self._capacity = float(burst)
        self._refill_per_second = per_minute / SECONDS_PER_MINUTE
        self._clock = clock
        self._max_tracked = max_tracked
        self._buckets: dict[int, _Bucket] = {}

    def check(self, key: int) -> RateDecision:
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._forget_idle(now)
            bucket = self._buckets[key] = _Bucket(tokens=self._capacity, updated_at=now)
        bucket.tokens = self._refilled(bucket, now)
        bucket.updated_at = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            bucket.warned = False
            return RateDecision.ALLOW
        if bucket.warned:
            return RateDecision.DENY
        bucket.warned = True
        return RateDecision.DENY_FIRST

    def _refilled(self, bucket: _Bucket, now: float) -> float:
        elapsed = max(0.0, now - bucket.updated_at)
        return min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)

    def _forget_idle(self, now: float) -> None:
        """Забыть клиентов, у которых ведро уже снова полное: о них нечего помнить."""
        if len(self._buckets) < self._max_tracked:
            return
        idle = [k for k, b in self._buckets.items() if self._refilled(b, now) >= self._capacity]
        for key in idle:
            del self._buckets[key]


def is_command(message: Message) -> bool:
    """Команда бота (`/start`, `/support@bot …`), а не обычное сообщение.

    `// заметка` и `/путь` — не команды: команда Telegram — латиница, цифры и `_`.
    """
    return message.text is not None and COMMAND_RE.match(message.text) is not None


class ThrottlingMiddleware(BaseMiddleware):
    """Не пускает к обработчикам слишком частые нажатия и сообщения одного клиента.

    Считаются нажатия кнопок и команды в личном чате с ботом. Обычные сообщения клиента —
    обращения в поддержку, у них свой предел (10 в минуту, `bot.support_guard`): альбом из
    нескольких фото не должен упираться в общий предел. Сообщения в группах не считаются:
    из чужих групп бот выходит, а в группе администраторов «подождите» не нужно.
    Администраторы (`exempt_ids`) не ограничены: им отвечать клиентам в поддержке.
    """

    def __init__(self, limiter: RateLimiter, texts: Texts, exempt_ids: frozenset[int]) -> None:
        self._limiter = limiter
        self._texts = texts
        self._exempt_ids = exempt_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            decision = self._decide(event.from_user.id)
            if decision is not RateDecision.ALLOW:
                # на кнопку отвечаем всегда, иначе у клиента будут «часики» на кнопке
                await event.answer(self._texts.get("throttle.callback"))
                return None
        elif (
            isinstance(event, Message)
            and event.chat.type == ChatType.PRIVATE
            and event.from_user is not None
            and is_command(event)
        ):
            decision = self._decide(event.from_user.id)
            if decision is RateDecision.DENY_FIRST:
                await event.answer(self._texts.get("throttle.message"))
            if decision is not RateDecision.ALLOW:
                return None
        return await handler(event, data)

    def _decide(self, user_id: int) -> RateDecision:
        if user_id in self._exempt_ids:
            return RateDecision.ALLOW
        return self._limiter.check(user_id)
