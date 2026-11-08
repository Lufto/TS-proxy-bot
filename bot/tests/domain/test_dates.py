from datetime import UTC, datetime, timedelta, timezone

import pytest

from tsproxy.domain.dates import add_months, format_moscow, require_aware


def test_format_moscow_example_from_spec() -> None:
    # 15:00 UTC = 18:00 по Москве (UTC+3)
    assert format_moscow(datetime(2026, 12, 10, 15, 0, tzinfo=UTC)) == "10.12.2026, 18:00 МСК"


def test_format_moscow_before_moscow_midnight_stays_same_day() -> None:
    assert format_moscow(datetime(2026, 12, 10, 20, 59, tzinfo=UTC)) == "10.12.2026, 23:59 МСК"


def test_format_moscow_at_moscow_midnight_moves_to_next_day() -> None:
    # 21:00 UTC — уже полночь следующего дня по Москве
    assert format_moscow(datetime(2026, 12, 10, 21, 0, tzinfo=UTC)) == "11.12.2026, 00:00 МСК"


def test_format_moscow_new_year_edge() -> None:
    assert format_moscow(datetime(2026, 12, 31, 21, 30, tzinfo=UTC)) == "01.01.2027, 00:30 МСК"


def test_format_moscow_accepts_other_timezones() -> None:
    novosibirsk = timezone(timedelta(hours=7))
    moment = datetime(2026, 12, 11, 1, 0, tzinfo=novosibirsk)  # = 10.12 18:00 UTC
    assert format_moscow(moment) == "10.12.2026, 21:00 МСК"


def test_format_moscow_drops_seconds() -> None:
    assert format_moscow(datetime(2026, 12, 10, 15, 0, 59, tzinfo=UTC)) == "10.12.2026, 18:00 МСК"


def test_format_moscow_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="часов"):
        format_moscow(datetime(2026, 12, 10, 15, 0))  # noqa: DTZ001


def test_require_aware_passes_aware_and_rejects_naive() -> None:
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    assert require_aware(aware) is aware
    with pytest.raises(ValueError):
        require_aware(datetime(2026, 1, 1))  # noqa: DTZ001


def test_add_months_simple() -> None:
    start = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
    assert add_months(start, 6) == datetime(2026, 7, 15, 10, 30, tzinfo=UTC)


def test_add_months_crosses_year() -> None:
    start = datetime(2026, 10, 24, tzinfo=UTC)
    assert add_months(start, 6) == datetime(2027, 4, 24, tzinfo=UTC)


def test_add_months_clamps_to_last_day_of_month() -> None:
    assert add_months(datetime(2026, 8, 31, tzinfo=UTC), 6) == datetime(2027, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2027, 8, 31, tzinfo=UTC), 6) == datetime(2028, 2, 29, tzinfo=UTC)
