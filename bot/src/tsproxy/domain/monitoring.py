"""Правила мониторинга (задача 22): расписание «раз в сутки по Москве», границы
отчётного дня и когда фоновая задача считается зависшей."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from tsproxy.domain.dates import MOSCOW, require_aware

# Задача «раз в N минут» считается зависшей, если не отработала успешно за 3 интервала…
STALE_MISSED_RUNS = 3
# …но не раньше чем через 5 минут: минутная задача может подождать медленный Telegram
MIN_STALE_AFTER = timedelta(minutes=5)
# Задача «раз в сутки» (бэкап, отчёт): сутки плюс 2 часа запаса — как у бэкапов панели
DAILY_JOB_STALE_AFTER = timedelta(hours=26)
ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class ReportDay:
    """Сутки по Москве: дата и границы [start, end) в UTC."""

    date: date
    start: datetime
    end: datetime


def next_daily_run(now: datetime, at: time) -> datetime:
    """Ближайший момент «в `at` по Москве» строго после `now`, в UTC."""
    local = require_aware(now).astimezone(MOSCOW)
    candidate = datetime.combine(local.date(), at, tzinfo=MOSCOW)
    if candidate <= local:
        candidate = datetime.combine(local.date() + ONE_DAY, at, tzinfo=MOSCOW)
    return candidate.astimezone(UTC)


def moscow_day(day: date) -> ReportDay:
    """Границы календарного дня по Москве в UTC."""
    start = datetime.combine(day, time(0, 0), tzinfo=MOSCOW)
    end = datetime.combine(day + ONE_DAY, time(0, 0), tzinfo=MOSCOW)
    return ReportDay(date=day, start=start.astimezone(UTC), end=end.astimezone(UTC))


def previous_moscow_day(now: datetime) -> ReportDay:
    """Вчерашний день по Москве — за него собирается ежедневный отчёт."""
    today = require_aware(now).astimezone(MOSCOW).date()
    return moscow_day(today - ONE_DAY)


def periodic_stale_after(interval: timedelta) -> timedelta:
    """Через сколько без успешного запуска задача «раз в `interval`» считается зависшей."""
    return max(interval * STALE_MISSED_RUNS, MIN_STALE_AFTER)


def is_job_stale(
    last_success: datetime | None,
    watched_since: datetime,
    stale_after: timedelta,
    now: datetime,
) -> bool:
    """Зависла ли задача: успеха не было дольше `stale_after`.

    Если успеха ещё не было вовсе, отсчёт — от начала наблюдения (запуска бота):
    иначе каждая задача считалась бы зависшей сразу после старта.
    """
    reference = last_success if last_success is not None else watched_since
    return require_aware(now) - require_aware(reference) > stale_after
