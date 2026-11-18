"""Дата клиенту: Telegram показывает её по часам клиента, старые версии — по Москве."""

from datetime import UTC, datetime

import pytest

from tsproxy.content.dates import client_time
from tsproxy.content.loader import Texts

MOMENT = datetime(2026, 12, 10, 15, 0, tzinfo=UTC)  # 18:00 по Москве
TAG = '<tg-time unix="1796914800" format="Dt">10.12.2026, 18:00 МСК</tg-time>'


def test_client_time_is_tg_time_tag_with_moscow_fallback() -> None:
    assert client_time(MOMENT) == TAG


def test_client_time_is_not_escaped_in_texts() -> None:
    texts = Texts({"until": "до <b>{at}</b>"}, "тест")

    assert texts.get("until", at=client_time(MOMENT)) == f"до <b>{TAG}</b>"


def test_client_time_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="без часового пояса"):
        client_time(datetime(2026, 12, 10, 15, 0))  # noqa: DTZ001
