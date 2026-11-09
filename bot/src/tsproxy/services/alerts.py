"""Тревоги администраторам в тему «🚨 Тревоги» группы администраторов (задача 18).

Правило склейки (одинаковые тревоги = одинаковый `key`):

1. Первая тревога с ключом уходит сразу и открывает окно склейки (`glue_window`, по умолчанию
   10 минут) — отсчёт от времени этой первой тревоги.
2. Повторы с тем же ключом внутри окна не отправляются, только считаются.
3. Когда окно закончилось и повторы были, уходит одна сводка «повторов за M мин: N»
   (текст `alerts.repeated`). Если «что случилось» за окно было разным (только у тревог
   через `alert`), сводка считает повторы по каждому: «×14 — HTTP 502, ×3 — не дождались…»;
   не влезшие в предел Telegram причины сводятся в одну строку «другие причины».
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

Тревоге с ключом из `buttons` под сообщение добавляется кнопка (задача сайта 15: «панель
не отвечает» → «Поставить плашку»). Кнопки задаёт сборка приложения, вызывающий код о них
не знает. Сводка о повторах уходит без кнопки: кнопка есть под первой тревогой.

Новый вызов `alert` (bot-alerts, задача 02) собирает тревогу из четырёх частей: что случилось
(по виду сбоя из ошибки, `failure.<вид>.what`), где заметили (`alert.<ключ>.where` и модуль.функция
переданного источника), чем грозит, куда смотреть (`failure.<вид>.causes`). Старый `raise_alert`
с готовым текстом пока остаётся (уберёт задача 09). Склейка у обоих — по ключу.

Для ежедневного отчёта (задача 22) тревоги считаются по дням (по Москве), вместе
со склеенными повторами. Счёт тоже в памяти — с запуска бота (`counting_since`).
"""

import html
import logging
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

from tsproxy.content.loader import Html, Texts
from tsproxy.domain.dates import MOSCOW
from tsproxy.integrations.failures import ServiceError
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.masking import Masker

logger = logging.getLogger(__name__)

DEFAULT_ALERT_GLUE_WINDOW = timedelta(minutes=10)
SECONDS_PER_MINUTE = 60
# Сколько дней помнить счёт тревог: отчёту нужен только вчерашний
ALERT_COUNT_KEEP_DAYS = 7
TELEGRAM_TEXT_LIMIT = 4096
# Запас под заголовок сводки повторов: в ней целиком повторяется первая тревога
SUMMARY_RESERVE = 200
CUT_MARK = "…"
# Сколько символов каждой подробности ошибки оставить в причине для сводки повторов
CAUSE_DETAIL_LIMIT = 100


@dataclass(frozen=True)
class AlertButton:
    """Кнопка под сообщением: надпись и данные нажатия (их разбирает обработчик админки)."""

    text: str
    callback_data: str


class AlertSender(Protocol):
    """Отправка сообщения в тему группы. Настоящая (aiogram) — в задаче 06, в тестах — подделка."""

    async def send(
        self, chat_id: int, thread_id: int | None, text: str, button: AlertButton | None = None
    ) -> None: ...


@dataclass
class _GluedAlert:
    """Окно склейки одного ключа: когда открыто, текст и «что случилось» первой тревоги,
    сколько было повторов и сколько из них с каким «что случилось»."""

    opened_at: datetime
    text: Html  # уже замаскированный и готовый для отправки
    cause: Html | None  # у тревог через старый `raise_alert` причины нет
    repeats: int = 0
    causes: Counter[Html] = field(default_factory=Counter)


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
        buttons: Mapping[str, AlertButton] | None = None,
    ) -> None:
        if glue_window <= timedelta(0):
            raise ValueError("Окно склейки тревог должно быть больше нуля")
        self._buttons = dict(buttons or {})
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
        `text` — обычный текст без разметки. Никогда не бросает исключение из-за отправки.

        Старый способ: новые тревоги — через `alert`."""
        masked = self._mask(text)
        await self._raise(key, Html(html.escape(masked)))

    async def alert(
        self,
        key: str,
        *,
        source: Callable[..., object],
        error: Exception | None = None,
        **values: object,
    ) -> None:
        """Поднять тревогу из описания `alert.<key>` в `alerts/`. `source` — функция, где заметили
        сбой (в тексте — её модуль и имя), `error` — что случилось, `values` — подстановки
        в описание. Склейка — по `key`. Никогда не бросает исключение из-за отправки."""
        body = self._compose(key, source, error, values)
        await self._raise(key, body, self._cause(error))

    async def _raise(self, key: str, body: Html, cause: Html | None = None) -> None:
        now = utc_now(self._clock)
        self._count(now)
        window = self._windows.get(key)
        if window is not None and not self._is_closed(window, now):
            window.repeats += 1
            if cause is not None:
                window.causes[cause] += 1
            return
        if window is not None:
            await self._close(key, window)
        # окно открываем до отправки: пока ждём Telegram, повторы уже склеиваются
        self._windows[key] = _GluedAlert(opened_at=now, text=body, cause=cause)
        await self._send(key, self._texts.get("alerts.prefix") + body, self._buttons.get(key))

    def _compose(
        self,
        key: str,
        source: Callable[..., object],
        error: Exception | None,
        values: Mapping[str, object],
    ) -> Html:
        """Текст тревоги не длиннее предела Telegram: урезаются подробности ошибки, не части."""
        limit = TELEGRAM_TEXT_LIMIT - len(self._texts.get("alerts.prefix")) - SUMMARY_RESERVE
        masked = {name: self._masked(value) for name, value in values.items()}
        details = self._error_details(error)
        text = self._render(key, source, error, masked, details)
        while len(text) > limit and details:
            name = max(details, key=lambda n: len(html.escape(details[n])))
            value = details[name]
            if len(value) <= len(CUT_MARK):
                break  # урезать уже нечего: длинные сами тексты из alerts/
            room = max(0, len(html.escape(value)) - (len(text) - limit) - len(CUT_MARK))
            details = {**details, name: _cut(value, room) + CUT_MARK}
            text = self._render(key, source, error, masked, details)
        return text

    def _masked(self, value: object) -> object:
        return self._mask(value) if isinstance(value, str) else value

    def _error_details(self, error: Exception | None) -> dict[str, str]:
        """Всё, что пришло из ошибки, — замаскированными строками (их можно урезать)."""
        if error is None:
            return {}
        details = {"error": str(error)}
        if isinstance(error, ServiceError) and error.failure is not None:
            details |= {name: str(value) for name, value in error.failure.details.items()}
        return {name: self._mask(value) for name, value in details.items()}

    def _render(
        self,
        key: str,
        source: Callable[..., object],
        error: Exception | None,
        values: Mapping[str, object],
        details: Mapping[str, str],
    ) -> Html:
        texts = self._texts
        lines = [texts.get(f"alert.{key}.title", **values)]
        if error is not None:
            what = self._what(error, details)
            lines.append(texts.get("alert_format.what", text=Html(what)))
        action = Html(texts.get(f"alert.{key}.where", **values))
        lines.append(texts.get("alert_format.where", action=action, source=_source_name(source)))
        threat = Html(texts.get(f"alert.{key}.threat", **values))
        lines.append(texts.get("alert_format.threat", text=threat))
        if isinstance(error, ServiceError) and error.failure is not None:
            lines.extend(self._causes(error.failure.kind))
        return Html("\n".join(lines))

    def _cause(self, error: Exception | None) -> Html | None:
        """«Что случилось» для счёта в сводке повторов — с короткими подробностями ошибки."""
        if error is None:
            return None
        details = {
            name: value
            if len(value) <= CAUSE_DETAIL_LIMIT
            else _cut(value, CAUSE_DETAIL_LIMIT) + CUT_MARK
            for name, value in self._error_details(error).items()
        }
        return Html(self._what(error, details))

    def _what(self, error: Exception, details: Mapping[str, str]) -> str:
        if isinstance(error, ServiceError) and error.failure is not None:
            return self._texts.get(f"failure.{error.failure.kind}.what", **details)
        return self._texts.get("alert_format.error", name=type(error).__name__, **details)

    def _causes(self, kind: str) -> list[str]:
        prefix = f"failure.{kind}.causes"
        lines = [self._texts.get("alert_format.causes")]
        for number in self._texts.ids_under(prefix):
            lines.append(
                self._texts.get(
                    "alert_format.cause",
                    number=number,
                    cause=Html(self._texts.get(f"{prefix}.{number}.cause")),
                    check=Html(self._texts.get(f"{prefix}.{number}.check")),
                )
            )
        return lines

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
        prefix = self._texts.get("alerts.prefix")
        summary = self._summary(window, causes=Html(""))
        room = TELEGRAM_TEXT_LIMIT - len(prefix) - len(summary)
        if room > 0 and set(window.causes) - {window.cause}:
            summary = self._summary(window, causes=self._repeat_causes(window, room))
        await self._send(key, prefix + summary)

    def _summary(self, window: _GluedAlert, causes: Html) -> str:
        return self._texts.get(
            "alerts.repeated",
            count=window.repeats,
            minutes=int(self._glue_window.total_seconds()) // SECONDS_PER_MINUTE,
            causes=causes,
            text=window.text,
        )

    def _repeat_causes(self, window: _GluedAlert, room: int) -> Html:
        """Строки «×N — что случилось», частые сверху, не длиннее `room` символов; не влезшие
        причины — одной строкой «другие причины»."""
        other_reserve = len("\n" + self._texts.get("alerts.repeated_other", count=window.repeats))
        lines: list[str] = []
        used = 0
        shown = 0
        for cause, count in window.causes.most_common():
            line = "\n" + self._texts.get("alerts.repeated_cause", count=count, cause=cause)
            if used + len(line) + other_reserve > room:
                break
            lines.append(line)
            used += len(line)
            shown += count
        if shown < window.repeats:
            other = self._texts.get("alerts.repeated_other", count=window.repeats - shown)
            lines.append("\n" + other)
        if used + other_reserve > room:
            return Html("")
        return Html("".join(lines))

    async def _send(self, key: str, message: str, button: AlertButton | None = None) -> None:
        logger.warning("Тревога %s: %s", key, message)
        try:
            await self._sender.send(self._chat_id, self._thread_id, message, button)
        # Ловим всё: отправка тревоги не должна ронять оплату или фоновую задачу,
        # а ошибки Telegram и сети бывают разных типов. Ошибку не прячем — пишем в лог.
        except Exception:
            logger.exception("Не удалось отправить тревогу %s в группу администраторов", key)


def _source_name(source: Callable[..., object]) -> str:
    """Место в коде для «где заметили»: `tsproxy.bot.connect.connect_screen`."""
    return f"{source.__module__}.{source.__qualname__}"


def _cut(value: str, room: int) -> str:
    """Начало строки, которое после экранирования для HTML займёт не больше `room` символов."""
    used = 0
    for index, char in enumerate(value):
        used += len(html.escape(char))
        if used > room:
            return value[:index]
    return value
