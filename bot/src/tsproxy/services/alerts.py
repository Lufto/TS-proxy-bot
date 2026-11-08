"""Тревоги администраторам в тему «🚨 Тревоги» группы администраторов (задача 18).

Правило склейки (одинаковые тревоги = одинаковый `key`):

1. Первая тревога с ключом уходит сразу и открывает окно склейки (`glue_window`, по умолчанию
   10 минут) — отсчёт от времени этой первой тревоги.
2. Повторы с тем же ключом внутри окна не отправляются, только считаются.
3. Когда окно закончилось и повторы были, уходит одна сводка «повторов за M мин: N»
   (текст `alerts.repeated`).
   Сводку отправляет `flush_repeats()` (его раз в минуту вызывает планировщик фоновых задач)
   или, если раньше пришла новая тревога с тем же ключом, сама `raise_alert` — сначала сводка
   за прошлое окно, потом новая тревога, которая открывает следующее окно.
4. Окно без повторов закрывается молча.

Итого при непрерывном потоке ошибок — не больше двух сообщений за окно на ключ.
Счётчики живут в памяти: после перезапуска бота первая тревога снова уйдёт сразу — это
приемлемо, перезапуск редок.

Тексты тревог проходят через маскировщик (секреты, ссылки подписки) и экранируются для HTML.
Сбой отправки не роняет вызывающий код: он пишется в лог как ошибка, а окно склейки всё равно
открывается, чтобы поток тревог не бил по недоступному Telegram.

Для ежедневного отчёта (задача 22) тревоги считаются по дням (по Москве), вместе
со склеенными повторами. Счёт тоже в памяти — с запуска бота (`counting_since`).
"""

import html
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from tsproxy.content.loader import Texts
from tsproxy.domain.dates import MOSCOW
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.masking import Masker

logger = logging.getLogger(__name__)

DEFAULT_ALERT_GLUE_WINDOW = timedelta(minutes=10)
SECONDS_PER_MINUTE = 60
# Сколько дней помнить счёт тревог: отчёту нужен только вчерашний
ALERT_COUNT_KEEP_DAYS = 7


class AlertSender(Protocol):
    """Отправка сообщения в тему группы. Настоящая (aiogram) — в задаче 06, в тестах — подделка."""

    async def send(self, chat_id: int, thread_id: int | None, text: str) -> None: ...


@dataclass
class _GluedAlert:
    """Окно склейки одного ключа: когда открыто, текст первой тревоги и сколько было повторов."""

    opened_at: datetime
    text: str  # уже замаскированный
    repeats: int = 0


class Alerter:
    """Поднимает тревоги в тему «🚨 Тревоги», склеивая одинаковые по ключу."""

    def __init__(
        self,
        sender: AlertSender,
        texts: Texts,
        chat_id: int,
        thread_id: int | None,
        clock: Clock,
        mask: Masker,
        glue_window: timedelta = DEFAULT_ALERT_GLUE_WINDOW,
    ) -> None:
        if glue_window <= timedelta(0):
            raise ValueError("Окно склейки тревог должно быть больше нуля")
        self._sender = sender
        self._texts = texts
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._clock = clock
        self._mask = mask
        self._glue_window = glue_window
        self._windows: dict[str, _GluedAlert] = {}
        self._counting_since = utc_now(clock)
        self._daily_counts: Counter[date] = Counter()

    @property
    def counting_since(self) -> datetime:
        """С какого момента считаются тревоги (запуск бота)."""
        return self._counting_since

    def raised_on(self, day: date) -> int:
        """Сколько тревог (с повторами) поднято за день по Москве."""
        return self._daily_counts[day]

    async def raise_alert(self, key: str, text: str) -> None:
        """Поднять тревогу. `key` — что считать одинаковым (например, `remnawave_unavailable`),
        `text` — обычный текст без разметки. Никогда не бросает исключение из-за отправки."""
        now = utc_now(self._clock)
        self._count(now)
        window = self._windows.get(key)
        if window is not None and not self._is_closed(window, now):
            window.repeats += 1
            return
        if window is not None:
            await self._close(key, window)
        masked = self._mask(text)
        # окно открываем до отправки: пока ждём Telegram, повторы уже склеиваются
        self._windows[key] = _GluedAlert(opened_at=now, text=masked)
        await self._send(key, self._texts.get("alerts.prefix") + html.escape(masked))

    async def flush_repeats(self) -> None:
        """Закрыть окна, время которых вышло, и отправить сводки о повторах."""
        now = utc_now(self._clock)
        closed = [(k, w) for k, w in self._windows.items() if self._is_closed(w, now)]
        for key, window in closed:
            await self._close(key, window)

    def _count(self, now: datetime) -> None:
        today = now.astimezone(MOSCOW).date()
        self._daily_counts[today] += 1
        oldest_kept = today - timedelta(days=ALERT_COUNT_KEEP_DAYS)
        for day in [day for day in self._daily_counts if day < oldest_kept]:
            del self._daily_counts[day]

    def _is_closed(self, window: _GluedAlert, now: datetime) -> bool:
        return now >= window.opened_at + self._glue_window

    async def _close(self, key: str, window: _GluedAlert) -> None:
        del self._windows[key]
        if window.repeats == 0:
            return
        summary = self._texts.get(
            "alerts.repeated",
            count=window.repeats,
            minutes=int(self._glue_window.total_seconds()) // SECONDS_PER_MINUTE,
            text=window.text,
        )
        await self._send(key, self._texts.get("alerts.prefix") + summary)

    async def _send(self, key: str, message: str) -> None:
        logger.warning("Тревога %s: %s", key, message)
        try:
            await self._sender.send(self._chat_id, self._thread_id, message)
        # Ловим всё: отправка тревоги не должна ронять оплату или фоновую задачу,
        # а ошибки Telegram и сети бывают разных типов. Ошибку не прячем — пишем в лог.
        except Exception:
            logger.exception("Не удалось отправить тревогу %s в группу администраторов", key)
