from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.profile import (
    PluralForm,
    ProfileStatus,
    TimeLeft,
    format_gigabytes,
    plural_form,
    profile_status,
    time_left,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
GB = 1024**3


def snapshot(
    expire_at: datetime | None = NOW + timedelta(days=10),
    *,
    on_trial: bool = False,
    grace_started_at: datetime | None = None,
    access_disabled: bool = False,
) -> ClientSnapshot:
    return ClientSnapshot(
        expire_at=expire_at,
        on_trial=on_trial,
        grace_started_at=grace_started_at,
        access_disabled=access_disabled,
    )


# --- Статус словами ---


def test_paid_subscription_in_future_is_active() -> None:
    assert profile_status(snapshot(), NOW, traffic_used_up=False) is ProfileStatus.ACTIVE


def test_trial_in_future_is_trial() -> None:
    status = profile_status(snapshot(on_trial=True), NOW, traffic_used_up=False)
    assert status is ProfileStatus.TRIAL


def test_trial_with_traffic_used_up_says_so() -> None:
    # 7 ГБ кончились раньше срока: клиенту важнее это, чем «пробный до …»
    status = profile_status(snapshot(on_trial=True), NOW, traffic_used_up=True)
    assert status is ProfileStatus.TRIAL_TRAFFIC_USED_UP


def test_traffic_used_up_matters_only_on_trial() -> None:
    assert profile_status(snapshot(), NOW, traffic_used_up=True) is ProfileStatus.ACTIVE


def test_grace_period() -> None:
    client = snapshot(NOW + timedelta(days=2), grace_started_at=NOW - timedelta(days=1))
    assert profile_status(client, NOW, traffic_used_up=False) is ProfileStatus.GRACE


def test_past_expiry_is_expired() -> None:
    client = snapshot(NOW - timedelta(minutes=1))
    assert profile_status(client, NOW, traffic_used_up=False) is ProfileStatus.EXPIRED


def test_expiry_exactly_now_is_expired() -> None:
    assert profile_status(snapshot(NOW), NOW, traffic_used_up=False) is ProfileStatus.EXPIRED


def test_disabled_after_end_is_expired() -> None:
    # Льготный или пробный кончился, бот выключил доступ — подписка просто закончилась
    client = snapshot(NOW - timedelta(days=1), access_disabled=True)
    assert profile_status(client, NOW, traffic_used_up=False) is ProfileStatus.EXPIRED


def test_disabled_with_time_left_is_suspended() -> None:
    # Срок ещё идёт, а доступ выключен — это не «продлите», а вопрос к поддержке
    client = snapshot(NOW + timedelta(days=5), access_disabled=True)
    assert profile_status(client, NOW, traffic_used_up=False) is ProfileStatus.SUSPENDED


def test_disabled_after_grace_is_expired_even_if_panel_date_is_later() -> None:
    client = snapshot(
        NOW + timedelta(days=1),
        grace_started_at=NOW - timedelta(days=10),
        access_disabled=True,
    )
    assert profile_status(client, NOW, traffic_used_up=False) is ProfileStatus.EXPIRED


def test_without_account_no_subscription() -> None:
    status = profile_status(snapshot(None), NOW, traffic_used_up=False)
    assert status is ProfileStatus.NO_SUBSCRIPTION


# --- Сколько осталось ---


def test_time_left_in_days_and_hours() -> None:
    until = NOW + timedelta(days=5, hours=3, minutes=59)
    assert time_left(NOW, until) == TimeLeft(days=5, hours=3)


def test_time_left_less_than_hour() -> None:
    assert time_left(NOW, NOW + timedelta(minutes=59)) == TimeLeft(days=0, hours=0)


def test_time_left_in_past_is_zero() -> None:
    assert time_left(NOW, NOW - timedelta(days=3)) == TimeLeft(days=0, hours=0)


def test_time_left_needs_timezone() -> None:
    with pytest.raises(ValueError, match="без часового пояса"):
        time_left(NOW.replace(tzinfo=None), NOW)


# --- Склонение: 1 день, 2 дня, 5 дней ---


@pytest.mark.parametrize(
    ("number", "form"),
    [
        (1, PluralForm.ONE),
        (21, PluralForm.ONE),
        (101, PluralForm.ONE),
        (2, PluralForm.FEW),
        (4, PluralForm.FEW),
        (22, PluralForm.FEW),
        (0, PluralForm.MANY),
        (5, PluralForm.MANY),
        (11, PluralForm.MANY),
        (12, PluralForm.MANY),
        (14, PluralForm.MANY),
        (111, PluralForm.MANY),
        (25, PluralForm.MANY),
    ],
)
def test_plural_form(number: int, form: PluralForm) -> None:
    assert plural_form(number) is form


# --- Гигабайты для клиента ---


@pytest.mark.parametrize(
    ("used_bytes", "expected"),
    [
        (0, "0"),
        (7 * GB, "7"),
        (GB * 23 // 10, "2,3"),
        (GB // 2, "0,5"),
        (GB * 1049 // 1000, "1"),  # 1,049 → 1,0 → «1»
        (GB * 1051 // 1000, "1,1"),
        (GB // 100, "0,1"),  # 10 МБ — не «0», иначе кажется, что трафик не тратится
    ],
)
def test_format_gigabytes(used_bytes: int, expected: str) -> None:
    assert format_gigabytes(used_bytes) == expected


def test_format_gigabytes_rejects_negative() -> None:
    with pytest.raises(ValueError, match="отрицательн"):
        format_gigabytes(-1)
