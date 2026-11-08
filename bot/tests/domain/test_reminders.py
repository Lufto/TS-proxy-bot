from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.grace import grace_ends_at
from tsproxy.domain.reminders import (
    AccessPeriod,
    Reminder,
    ReminderKind,
    TrialTrafficNotice,
    due_reminder,
    trial_traffic_notice,
)
from tsproxy.domain.trial import trial_extension_end

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
GB = 1024**3
TRIAL_LIMIT = 7 * GB
# первый байт, с которого израсходовано не меньше 80% (округление вверх)
WARNING_FROM = -(-TRIAL_LIMIT * 8 // 10)


def snapshot(
    expire_at: datetime | None,
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


def reminder(
    client: ClientSnapshot,
    now: datetime = NOW,
    *,
    ever_connected: bool = True,
    traffic_used_up: bool = False,
) -> Reminder | None:
    return due_reminder(client, now, ever_connected=ever_connected, traffic_used_up=traffic_used_up)


# --- Напоминания за день и за час ---


@pytest.mark.parametrize("left", [timedelta(days=1), timedelta(hours=23), timedelta(minutes=61)])
def test_day_before_reminder_in_last_day(left: timedelta) -> None:
    expire_at = NOW + left
    assert reminder(snapshot(expire_at)) == Reminder(
        ReminderKind.DAY_BEFORE, AccessPeriod.PAID, expire_at
    )


@pytest.mark.parametrize("left", [timedelta(hours=1), timedelta(minutes=30), timedelta(seconds=1)])
def test_hour_before_reminder_in_last_hour(left: timedelta) -> None:
    expire_at = NOW + left
    assert reminder(snapshot(expire_at)) == Reminder(
        ReminderKind.HOUR_BEFORE, AccessPeriod.PAID, expire_at
    )


@pytest.mark.parametrize("left", [timedelta(days=1, seconds=1), timedelta(days=5)])
def test_no_reminder_earlier_than_a_day(left: timedelta) -> None:
    assert reminder(snapshot(NOW + left)) is None


@pytest.mark.parametrize("left", [timedelta(0), -timedelta(minutes=5)])
def test_no_reminder_after_expiry(left: timedelta) -> None:
    # окончание — дело льготного периода (задача 11) или правил пробного
    assert reminder(snapshot(NOW + left)) is None


def test_no_reminder_without_account() -> None:
    assert reminder(snapshot(None)) is None


def test_no_reminder_when_access_disabled() -> None:
    # доступ выключил администратор — «продлите» было бы неправдой
    assert reminder(snapshot(NOW + timedelta(hours=5), access_disabled=True)) is None


def test_connected_trial_gets_trial_reminder() -> None:
    expire_at = NOW + timedelta(hours=5)
    assert reminder(snapshot(expire_at, on_trial=True)) == Reminder(
        ReminderKind.DAY_BEFORE, AccessPeriod.TRIAL, expire_at
    )


def test_never_connected_trial_gets_no_reminder() -> None:
    # не подключался — пробный продлят сами, напоминать «купите» рано (задача 05)
    client = snapshot(NOW + timedelta(minutes=10), on_trial=True)
    assert reminder(client, ever_connected=False) is None


def test_trial_with_used_up_traffic_gets_no_reminder() -> None:
    # доступа уже нет из-за трафика — об этом своё сообщение
    client = snapshot(NOW + timedelta(hours=5), on_trial=True)
    assert reminder(client, traffic_used_up=True) is None


def test_paid_reminder_ignores_connection() -> None:
    expire_at = NOW + timedelta(hours=5)
    assert reminder(snapshot(expire_at), ever_connected=False) is not None


@pytest.mark.parametrize("left", [timedelta(days=1), timedelta(minutes=30)])
def test_grace_gets_no_reminder(left: timedelta) -> None:
    # в льготный период напоминает задача 11: раз в день, «последний день» и финал
    started = NOW + left - timedelta(days=4)
    client = snapshot(grace_ends_at(started), grace_started_at=started)
    assert reminder(client) is None


def test_reminder_requires_aware_time() -> None:
    with pytest.raises(ValueError, match="часового пояса"):
        reminder(snapshot(NOW), NOW.replace(tzinfo=None))


# --- Трафик пробного ---


def traffic(
    used: int,
    *,
    limit: int = TRIAL_LIMIT,
    used_up: bool = False,
    client: ClientSnapshot | None = None,
) -> TrialTrafficNotice | None:
    trial = client or snapshot(NOW + timedelta(days=3), on_trial=True)
    return trial_traffic_notice(
        trial, NOW, used_bytes=used, limit_bytes=limit, traffic_used_up=used_up
    )


@pytest.mark.parametrize("used", [0, GB, WARNING_FROM - 1])
def test_no_traffic_notice_below_80_percent(used: int) -> None:
    assert traffic(used) is None


@pytest.mark.parametrize("used", [WARNING_FROM, TRIAL_LIMIT - 1])
def test_traffic_warning_from_80_percent(used: int) -> None:
    assert traffic(used) is TrialTrafficNotice.WARNING


def test_traffic_used_up_at_limit() -> None:
    assert traffic(TRIAL_LIMIT) is TrialTrafficNotice.USED_UP


def test_traffic_used_up_when_panel_limited_account() -> None:
    # панель отключила аккаунт по трафику, даже если счётчик чуть отстаёт
    assert traffic(TRIAL_LIMIT - GB, used_up=True) is TrialTrafficNotice.USED_UP


def test_no_traffic_notice_without_limit() -> None:
    assert traffic(100 * GB, limit=0) is None


def test_no_traffic_notice_outside_trial() -> None:
    assert traffic(TRIAL_LIMIT, client=snapshot(NOW + timedelta(days=3))) is None


def test_no_traffic_notice_after_trial_expired() -> None:
    expired = snapshot(NOW - timedelta(minutes=1), on_trial=True)
    assert traffic(TRIAL_LIMIT, client=expired) is None


# --- Продление пробного ---


def test_trial_extension_counts_from_expiry() -> None:
    assert trial_extension_end(NOW, NOW) == NOW + timedelta(days=3)


def test_late_trial_extension_counts_from_now() -> None:
    # бот лежал — клиент всё равно получает 3 полных дня
    assert trial_extension_end(NOW - timedelta(days=1), NOW) == NOW + timedelta(days=3)
