"""Правила поддержки (задача 16 в `.scratch/vpn-bot-rework/issues/`).

Обращение — тема в группе администраторов. Сообщение администратора в теме уходит клиенту,
кроме внутренних заметок: они начинаются с `//`.
"""

NOTE_PREFIX = "//"
# Больше стольких сообщений в минуту от клиента — просим подождать (решение владельца)
SUPPORT_MESSAGES_PER_MINUTE = 10
# Telegram: название темы — от 1 до 128 символов
TOPIC_NAME_MAX_LENGTH = 128
ELLIPSIS = "…"


def is_internal_note(text: str | None) -> bool:
    """Внутренняя заметка администратора: текст (или подпись) начинается с `//`."""
    return text is not None and text.lstrip().startswith(NOTE_PREFIX)


def fit_topic_name(name: str) -> str:
    """Название темы в одну строку и не длиннее предела Telegram."""
    flat = " ".join(name.split())
    if len(flat) <= TOPIC_NAME_MAX_LENGTH:
        return flat
    return flat[: TOPIC_NAME_MAX_LENGTH - len(ELLIPSIS)] + ELLIPSIS
