from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.trial import (
    TrialEnd,
    TrialExpiryAction,
    decide_on_trial_expiry,
    max_trial_duration,
    paid_access_limits,
    start_trial,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
GB = 1024**3


def trial_end(
    expire_at: datetime,
    *,
    ever_connected: bool = False,
    traffic_limit_reached: bool = False,
    extensions_used: int = 0,
) -> TrialEnd:
    return TrialEnd(
        expire_at=expire_at,
        ever_connected=ever_connected,
        traffic_limit_reached=traffic_limit_reached,
        extensions_used=extensions_used,
    )


def test_start_trial_seven_days_seven_gb_three_devices() -> None:
    grant = start_trial(NOW)
    assert grant.expire_at == NOW + timedelta(days=7)
    assert grant.traffic_limit_bytes == 7 * GB
    assert grant.device_limit == 3


def test_paid_access_has_no_traffic_limit_and_three_devices() -> None:
    # Покупка в пробный: снять лимит 7 ГБ, устройства как у тарифа (аудит §7.6)
    limits = paid_access_limits()
    assert limits.traffic_limit_bytes == 0
    assert limits.device_limit == 3


def test_never_connected_trial_is_extended_by_three_days() -> None:
    decision = decide_on_trial_expiry(trial_end(NOW), NOW)
    assert decision.action is TrialExpiryAction.EXTEND
    assert decision.new_expire_at == NOW + timedelta(days=3)
    assert decision.extensions_used == 1


def test_late_check_extends_from_now() -> None:
    # Бот проверил окончание с опозданием — клиент всё равно получает 3 полных дня
    decision = decide_on_trial_expiry(trial_end(NOW - timedelta(hours=5)), NOW)
    assert decision.new_expire_at == NOW + timedelta(days=3)


def test_fifth_extension_is_the_last_one() -> None:
    decision = decide_on_trial_expiry(trial_end(NOW, extensions_used=4), NOW)
    assert decision.action is TrialExpiryAction.EXTEND
    assert decision.extensions_used == 5


def test_after_five_extensions_farewell() -> None:
    decision = decide_on_trial_expiry(trial_end(NOW, extensions_used=5), NOW)
    assert decision.action is TrialExpiryAction.FAREWELL
    assert decision.new_expire_at is None


def test_connected_client_gets_no_extension_and_no_grace() -> None:
    decision = decide_on_trial_expiry(trial_end(NOW, ever_connected=True), NOW)
    assert decision.action is TrialExpiryAction.END


def test_traffic_limit_reached_counts_as_connected() -> None:
    # Пробный кончился через LIMITED: 7 ГБ израсходованы — значит подключался (аудит §7.6)
    decision = decide_on_trial_expiry(trial_end(NOW, traffic_limit_reached=True), NOW)
    assert decision.action is TrialExpiryAction.END


def test_connected_during_extension_ends_without_further_extensions() -> None:
    state = trial_end(NOW, ever_connected=True, extensions_used=2)
    assert decide_on_trial_expiry(state, NOW).action is TrialExpiryAction.END


def test_trial_not_yet_over_does_nothing() -> None:
    state = trial_end(NOW + timedelta(minutes=1))
    assert decide_on_trial_expiry(state, NOW).action is TrialExpiryAction.NOT_EXPIRED


def test_repeated_event_after_extension_does_nothing() -> None:
    # Повторное событие пришло, когда срок уже продлён: в панели дата в будущем
    first = decide_on_trial_expiry(trial_end(NOW), NOW)
    assert first.new_expire_at is not None
    repeat = trial_end(first.new_expire_at, extensions_used=first.extensions_used)
    assert decide_on_trial_expiry(repeat, NOW).action is TrialExpiryAction.NOT_EXPIRED


def test_maximum_trial_is_22_days() -> None:
    assert max_trial_duration() == timedelta(days=22)


def test_full_chain_of_extensions_ends_with_farewell_on_day_22() -> None:
    expire_at = start_trial(NOW).expire_at
    used = 0
    while True:
        decision = decide_on_trial_expiry(trial_end(expire_at, extensions_used=used), expire_at)
        if decision.action is not TrialExpiryAction.EXTEND:
            break
        assert decision.new_expire_at is not None
        expire_at, used = decision.new_expire_at, decision.extensions_used
    assert decision.action is TrialExpiryAction.FAREWELL
    assert expire_at == NOW + timedelta(days=22)
    assert used == 5


def test_negative_extensions_rejected() -> None:
    with pytest.raises(ValueError):
        decide_on_trial_expiry(trial_end(NOW, extensions_used=-1), NOW)
