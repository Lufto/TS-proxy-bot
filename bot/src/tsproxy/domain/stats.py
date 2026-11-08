"""Статистика админки (задача 19): чистые правила подсчёта.

«За день» и «за месяц» — календарные, по Москве: продажи с 00:00 МСК сегодня и с первого
числа текущего месяца. Конверсия пробного — доля клиентов с пробным, которые потом
заплатили, округлённая вниз (не приукрашиваем). Время ответа поддержки — от открытия
обращения до первого ответа администратора.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tsproxy.domain.constants import PERCENT
from tsproxy.domain.dates import MOSCOW, require_aware
from tsproxy.domain.subscription import SubscriptionPhase

SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60


def moscow_day_start(now: datetime) -> datetime:
    """Начало сегодняшнего дня по Москве (00:00 МСК) — в UTC."""
    local = require_aware(now).astimezone(MOSCOW)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def moscow_month_start(now: datetime) -> datetime:
    """Начало текущего месяца по Москве (1-е число, 00:00 МСК) — в UTC."""
    local = require_aware(now).astimezone(MOSCOW)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def conversion_percent(trials: int, converted: int) -> int | None:
    """Сколько процентов клиентов с пробным потом заплатили. `None` — пробных не было."""
    if trials < 0 or not 0 <= converted <= trials:
        raise ValueError(f"Неверные числа для конверсии: {converted} из {trials}")
    if trials == 0:
        return None
    return converted * PERCENT // trials


@dataclass(frozen=True)
class ResponseTimes:
    """Время первого ответа поддержки: в среднем, дольше всего и по скольким обращениям."""

    average: timedelta
    longest: timedelta
    count: int


def response_times(durations: Iterable[timedelta]) -> ResponseTimes | None:
    """Сводка по временам ответа. `None` — ответов не было."""
    values = list(durations)
    if any(value < timedelta(0) for value in values):
        raise ValueError("Время ответа не может быть отрицательным")
    if not values:
        return None
    total = sum(values, timedelta(0))
    return ResponseTimes(average=total / len(values), longest=max(values), count=len(values))


def split_minutes(duration: timedelta) -> tuple[int, int]:
    """Часы и минуты для показа; секунды отбрасываются."""
    total_minutes = int(duration.total_seconds()) // SECONDS_PER_MINUTE
    return divmod(total_minutes, MINUTES_PER_HOUR)


@dataclass(frozen=True)
class SubscriptionCounts:
    """Подписки, которые сейчас дают доступ: оплаченные, в льготном периоде и пробные."""

    paid: int
    grace: int
    trial: int


def tally_phases(phases: Iterable[SubscriptionPhase]) -> SubscriptionCounts:
    """Посчитать живые подписки по состояниям. Истёкшие и «аккаунта нет» не считаются."""
    counted = list(phases)
    return SubscriptionCounts(
        paid=counted.count(SubscriptionPhase.ACTIVE),
        grace=counted.count(SubscriptionPhase.GRACE),
        trial=counted.count(SubscriptionPhase.TRIAL),
    )
