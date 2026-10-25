"""Правила поддержки (задача 16 в `.scratch/vpn-bot-rework/issues/`).

Обращение — тема в группе администраторов. Сообщение администратора в теме уходит клиенту,
кроме внутренних заметок: они начинаются с `//`. Закрытое обращение переоткрывается, если
клиент пишет снова в течение суток; нет ответа поддержки дольше часа — напоминание в теме.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from tsproxy.domain.constants import ONE_DAY, ONE_HOUR
from tsproxy.domain.dates import require_aware

NOTE_PREFIX = "//"
# Больше стольких сообщений в минуту от клиента — просим подождать (решение владельца)
SUPPORT_MESSAGES_PER_MINUTE = 10
# Telegram: название темы — от 1 до 128 символов
TOPIC_NAME_MAX_LENGTH = 128
ELLIPSIS = "…"
# Клиент пишет в течение суток после закрытия — то же обращение, позже — новое (решение владельца)
REOPEN_WINDOW: timedelta = ONE_DAY
# Клиент ждёт ответа дольше часа — напоминание в теме (решение владельца)
REPLY_REMINDER_AFTER: timedelta = ONE_HOUR


def is_internal_note(text: str | None) -> bool:
    """Внутренняя заметка администратора: текст (или подпись) начинается с `//`."""
    return text is not None and text.lstrip().startswith(NOTE_PREFIX)


def fit_topic_name(name: str) -> str:
    """Название темы в одну строку и не длиннее предела Telegram."""
    flat = " ".join(name.split())
    if len(flat) <= TOPIC_NAME_MAX_LENGTH:
        return flat
    return flat[: TOPIC_NAME_MAX_LENGTH - len(ELLIPSIS)] + ELLIPSIS


def reopens(closed_at: datetime, now: datetime) -> bool:
    """Новое сообщение клиента переоткрывает закрытое обращение: прошло не больше суток."""
    return require_aware(now) - require_aware(closed_at) <= REOPEN_WINDOW


def waiting_since(
    client_messages_at: Iterable[datetime], handled_at: datetime | None
) -> datetime | None:
    """С какого момента клиент ждёт ответа: первое его сообщение после `handled_at`.

    `handled_at` — последний ответ поддержки или закрытие обращения (что позже): то, что
    клиент написал до закрытия, уже разобрано. `None` — никто не ждёт. Заметки `//`
    ответом не считаются — их сюда не передают.
    """
    unanswered = [
        require_aware(moment)
        for moment in client_messages_at
        if handled_at is None or moment > require_aware(handled_at)
    ]
    return min(unanswered, default=None)


def reply_overdue(waiting_from: datetime, now: datetime) -> bool:
    """Клиент ждёт ответа дольше часа — пора напомнить."""
    return require_aware(now) - require_aware(waiting_from) > REPLY_REMINDER_AFTER
