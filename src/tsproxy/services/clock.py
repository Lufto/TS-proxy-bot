"""Часы, которые передаются в сервисы снаружи: в тестах время двигает тест, а не машина."""

from collections.abc import Callable
from datetime import UTC, datetime

type Clock = Callable[[], datetime]


def system_clock() -> datetime:
    """Настоящее текущее время в UTC — для боевого запуска."""
    return datetime.now(UTC)


def utc_now(clock: Clock) -> datetime:
    """Спросить время у часов и убедиться, что оно с часовым поясом (в UTC)."""
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"Часы вернули время без часового пояса: {now!r}")
    return now.astimezone(UTC)
