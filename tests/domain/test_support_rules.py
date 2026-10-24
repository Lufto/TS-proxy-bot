"""Правила поддержки: внутренняя заметка `//` и название темы обращения."""

import pytest

from tsproxy.domain.support import TOPIC_NAME_MAX_LENGTH, fit_topic_name, is_internal_note


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
