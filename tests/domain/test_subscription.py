from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.subscription import (
    SubscriptionPhase,
    grant_target_expire_at,
    new_period_start,
    subscription_phase,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
DAYS_30 = timedelta(days=30)


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


# --- Состояние клиента ---


def test_phase_new_client_without_account() -> None:
    assert subscription_phase(snapshot(None), NOW) is SubscriptionPhase.NEW


def test_phase_active_paid() -> None:
    assert subscription_phase(snapshot(NOW + DAYS_30), NOW) is SubscriptionPhase.ACTIVE


def test_phase_trial() -> None:
    state = snapshot(NOW + timedelta(days=3), on_trial=True)
    assert subscription_phase(state, NOW) is SubscriptionPhase.TRIAL


def test_phase_trial_after_its_end_is_expired() -> None:
    state = snapshot(NOW - timedelta(minutes=1), on_trial=True)
    assert subscription_phase(state, NOW) is SubscriptionPhase.EXPIRED


def test_phase_at_exact_expiry_moment_is_expired() -> None:
    assert subscription_phase(snapshot(NOW), NOW) is SubscriptionPhase.EXPIRED


def test_phase_grace_even_though_panel_shows_future_expiry() -> None:
    # В льготный период в Remnawave срок продлён на 3 дня, но подписка уже не оплачена
    started = NOW - timedelta(days=1)
    state = snapshot(started + timedelta(days=3), grace_started_at=started)
    assert subscription_phase(state, NOW) is SubscriptionPhase.GRACE


def test_phase_grace_over_is_expired() -> None:
    started = NOW - timedelta(days=4)  # 3 дня + «последний день»
    state = snapshot(NOW, grace_started_at=started)
    assert subscription_phase(state, NOW) is SubscriptionPhase.EXPIRED


def test_phase_disabled_access_is_expired() -> None:
    state = snapshot(NOW - timedelta(days=10), access_disabled=True)
    assert subscription_phase(state, NOW) is SubscriptionPhase.EXPIRED


def test_phase_rejects_naive_now() -> None:
    with pytest.raises(ValueError):
        subscription_phase(snapshot(None), datetime(2026, 1, 1))  # noqa: DTZ001


# --- От какой даты считать новый срок (задача 04, правило 2) ---


def test_start_active_adds_to_current_expiry() -> None:
    expire_at = NOW + timedelta(days=5)
    assert new_period_start(SubscriptionPhase.ACTIVE, NOW, expire_at) == expire_at


def test_start_trial_adds_to_trial_expiry() -> None:
    expire_at = NOW + timedelta(days=2, hours=3)
    assert new_period_start(SubscriptionPhase.TRIAL, NOW, expire_at) == expire_at


@pytest.mark.parametrize(
    "phase", [SubscriptionPhase.GRACE, SubscriptionPhase.EXPIRED, SubscriptionPhase.NEW]
)
def test_start_from_payment_moment(phase: SubscriptionPhase) -> None:
    # В льготный период Remnawave показывает срок в будущем — его не используем
    assert new_period_start(phase, NOW, NOW + timedelta(days=2)) == NOW


def test_start_active_with_stale_expiry_uses_payment_moment() -> None:
    # Срок уже прошёл, а событие об окончании ещё не обработано — клиент не теряет время
    assert new_period_start(SubscriptionPhase.ACTIVE, NOW, NOW - timedelta(hours=1)) == NOW


def test_start_new_client_without_expiry() -> None:
    assert new_period_start(SubscriptionPhase.NEW, NOW, None) == NOW


# --- «Продлить до X» — абсолютная дата в момент оплаты (задача 21, п. 6) ---


def test_target_purchase_during_active_subscription() -> None:
    state = snapshot(NOW + timedelta(days=5))
    assert grant_target_expire_at(state, NOW, DAYS_30) == NOW + timedelta(days=35)


def test_target_purchase_during_trial_adds_to_trial_rest() -> None:
    state = snapshot(NOW + timedelta(days=4), on_trial=True)
    assert grant_target_expire_at(state, NOW, DAYS_30) == NOW + timedelta(days=34)


def test_target_purchase_during_grace_counts_from_payment() -> None:
    started = NOW - timedelta(days=2)
    state = snapshot(started + timedelta(days=3), grace_started_at=started)
    assert grant_target_expire_at(state, NOW, DAYS_30) == NOW + DAYS_30


def test_target_purchase_after_access_disabled() -> None:
    state = snapshot(NOW - timedelta(days=40), access_disabled=True)
    assert grant_target_expire_at(state, NOW, DAYS_30) == NOW + DAYS_30


def test_target_new_client() -> None:
    assert grant_target_expire_at(snapshot(None), NOW, DAYS_30) == NOW + DAYS_30


def test_target_purchase_during_grace_adds_carried_compensation() -> None:
    started = NOW - timedelta(days=1)
    state = snapshot(started + timedelta(days=3), grace_started_at=started)
    carried = timedelta(days=1, hours=5)
    target = grant_target_expire_at(state, NOW, DAYS_30, carried_compensation=carried)
    assert target == NOW + DAYS_30 + carried


def test_target_is_stable_for_the_same_inputs() -> None:
    # Повтор выдачи использует сохранённую дату, но и сам расчёт детерминирован
    state = snapshot(NOW + timedelta(days=5))
    assert grant_target_expire_at(state, NOW, DAYS_30) == grant_target_expire_at(
        state, NOW, DAYS_30
    )


def test_target_rejects_non_positive_duration() -> None:
    with pytest.raises(ValueError):
        grant_target_expire_at(snapshot(None), NOW, timedelta(0))


def test_target_rejects_negative_compensation() -> None:
    with pytest.raises(ValueError):
        grant_target_expire_at(
            snapshot(None), NOW, DAYS_30, carried_compensation=timedelta(hours=-1)
        )
