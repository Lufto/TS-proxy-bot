"""Правила поддержки: заметка `//`, название темы, переоткрытие и напоминание об ответе."""

from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.support import (
    REOPEN_WINDOW,
    REPLY_REMINDER_AFTER,
    TOPIC_NAME_MAX_LENGTH,
    fit_topic_name,
    is_internal_note,
    reopens,
    reply_overdue,
    waiting_since,
)

CLOSED_AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("// клиент оплатил, проверить", True),
        ("//без пробела", True),
        ("  // с отступом", True),
        ("Ответ клиенту: https://ts-proxy.com//terms", False),
        ("/ одна черта", False),
        ("", False),
        (None, False),
    ],
)
def test_internal_note_starts_with_two_slashes(text: str | None, expected: bool) -> None:
    assert is_internal_note(text) is expected


def test_short_topic_name_is_kept() -> None:
    assert fit_topic_name("#12 Иван (@ivan)") == "#12 Иван (@ivan)"


def test_long_topic_name_is_cut_to_telegram_limit() -> None:
    name = fit_topic_name("#12 " + "Я" * 500)
    assert len(name) == TOPIC_NAME_MAX_LENGTH
    assert name.startswith("#12 ")
    assert name.endswith("…")


def test_topic_name_has_no_line_breaks() -> None:
    assert fit_topic_name("#12 Иван\nПетров") == "#12 Иван Петров"


# --- Переоткрытие в течение суток (задача 17) ---


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (timedelta(minutes=5), True),
        (timedelta(hours=23), True),
        (REOPEN_WINDOW, True),  # ровно сутки — ещё «в течение суток»
        (REOPEN_WINDOW + timedelta(seconds=1), False),
        (timedelta(hours=25), False),
    ],
)
def test_message_within_a_day_after_closing_reopens(after: timedelta, expected: bool) -> None:
    assert reopens(CLOSED_AT, CLOSED_AT + after) is expected


def test_reopen_needs_time_zone() -> None:
    with pytest.raises(ValueError, match="часового пояса"):
        reopens(CLOSED_AT.replace(tzinfo=None), CLOSED_AT)


# --- Кто ждёт ответа ---


def test_client_waits_since_first_message_after_last_reply_or_closing() -> None:
    first, second = CLOSED_AT, CLOSED_AT + timedelta(minutes=10)
    reply = CLOSED_AT + timedelta(minutes=20)
    third, fourth = reply + timedelta(minutes=1), reply + timedelta(minutes=2)

    assert waiting_since([first, second], handled_at=None) == first
    assert waiting_since([first, second], handled_at=reply) is None  # ответили
    assert waiting_since([first, second, third, fourth], handled_at=reply) == third


def test_no_client_messages_means_nobody_waits() -> None:
    assert waiting_since([], handled_at=None) is None


def test_messages_order_does_not_matter() -> None:
    later, earlier = CLOSED_AT + timedelta(hours=1), CLOSED_AT
    assert waiting_since([later, earlier], handled_at=None) == earlier


@pytest.mark.parametrize(
    ("waited", "expected"),
    [
        (timedelta(minutes=59), False),
        (REPLY_REMINDER_AFTER, False),  # «дольше часа» — строго больше
        (REPLY_REMINDER_AFTER + timedelta(seconds=1), True),
        (timedelta(hours=5), True),
    ],
)
def test_reminder_after_more_than_an_hour_without_reply(waited: timedelta, expected: bool) -> None:
    assert reply_overdue(CLOSED_AT, CLOSED_AT + waited) is expected
