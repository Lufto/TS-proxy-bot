"""Перевыпуск ссылки клиентом — не чаще раза в сутки (задача 31)."""

from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.link_revoke import link_revoke_available_at

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def test_never_revoked_is_allowed() -> None:
    assert link_revoke_available_at(NOW, None) is None


def test_revoked_less_than_a_day_ago_is_refused_until_a_day_passes() -> None:
    last = NOW - timedelta(hours=3)
    assert link_revoke_available_at(NOW, last) == last + timedelta(days=1)


def test_exactly_a_day_later_is_allowed() -> None:
    assert link_revoke_available_at(NOW, NOW - timedelta(days=1)) is None


def test_more_than_a_day_later_is_allowed() -> None:
    assert link_revoke_available_at(NOW, NOW - timedelta(days=3)) is None


def test_available_moment_is_rounded_up_to_a_minute() -> None:
    # Клиенту показываем время без секунд: в показанную минуту перевыпуск уже должен пройти
    last = NOW - timedelta(hours=1) + timedelta(seconds=30, microseconds=5)
    available_at = link_revoke_available_at(NOW, last)
    assert available_at == NOW - timedelta(hours=1) + timedelta(days=1, minutes=1)
    assert link_revoke_available_at(available_at - timedelta(seconds=1), last) is not None
    assert link_revoke_available_at(available_at, last) is None


def test_naive_dates_are_rejected() -> None:
    with pytest.raises(ValueError):
        link_revoke_available_at(NOW.replace(tzinfo=None), None)
    with pytest.raises(ValueError):
        link_revoke_available_at(NOW, NOW.replace(tzinfo=None))
