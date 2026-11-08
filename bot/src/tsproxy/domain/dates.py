"""Даты: внутри всё в UTC, клиенту показываем по Москве."""

import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
# Формат из спецификации: «10.12.2026, 18:00 МСК»
CLIENT_DATE_FORMAT = "%d.%m.%Y, %H:%M МСК"
MONTHS_PER_YEAR = 12


def require_aware(moment: datetime) -> datetime:
    """Вернуть дату как есть или упасть, если у неё нет часового пояса.

    Дата без пояса значит «местное время какой-то машины» — считать по ней сроки нельзя.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"дата {moment!r} без часового пояса")
    return moment


def format_moscow(moment: datetime) -> str:
    """Дата для клиента по московскому времени: «10.12.2026, 18:00 МСК». Секунды отбрасываются."""
    return require_aware(moment).astimezone(MOSCOW).strftime(CLIENT_DATE_FORMAT)


def add_months(moment: datetime, months: int) -> datetime:
    """Прибавить календарные месяцы. 31 августа + 6 месяцев = последний день февраля."""
    require_aware(moment)
    month_index = moment.month - 1 + months
    year = moment.year + month_index // MONTHS_PER_YEAR
    month = month_index % MONTHS_PER_YEAR + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)
