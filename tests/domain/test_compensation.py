from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.compensation import (
    CompensationAction,
    Outage,
    compensation_duration,
    merge_outages,
    plan_compensation,
)
from tsproxy.domain.subscription import grant_target_expire_at

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def client(
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


# --- Сколько компенсировать ---


def test_compensation_is_outage_plus_one_day() -> None:
    outage = Outage(NOW - timedelta(hours=3), NOW)
    assert compensation_duration(outage) == timedelta(hours=3) + DAY


def test_partial_hour_of_outage_rounds_up() -> None:
    outage = Outage(NOW - timedelta(hours=2, minutes=1), NOW)
    assert compensation_duration(outage) == timedelta(hours=3) + DAY


def test_outage_must_end_after_start() -> None:
    with pytest.raises(ValueError):
        Outage(NOW, NOW)


# --- «Мигание» ноды — один инцидент (аудит §7.7) ---


def test_flapping_outages_merge_into_one_incident() -> None:
    outages = [
        Outage(NOW, NOW + timedelta(minutes=5)),
        Outage(NOW + timedelta(minutes=20), NOW + timedelta(minutes=25)),
        Outage(NOW + timedelta(minutes=50), NOW + timedelta(minutes=55)),
    ]
    assert merge_outages(outages) == [Outage(NOW, NOW + timedelta(minutes=55))]


def test_outages_far_apart_stay_separate() -> None:
    first = Outage(NOW, NOW + timedelta(minutes=5))
    second = Outage(NOW + timedelta(hours=3), NOW + timedelta(hours=4))
    assert merge_outages([second, first]) == [first, second]


def test_gap_exactly_at_threshold_merges() -> None:
    first = Outage(NOW, NOW + timedelta(minutes=10))
    second = Outage(NOW + timedelta(hours=1, minutes=10), NOW + timedelta(hours=2))
    assert merge_outages([first, second]) == [Outage(NOW, NOW + timedelta(hours=2))]


def test_overlapping_outages_merge() -> None:
    first = Outage(NOW, NOW + timedelta(hours=2))
    inner = Outage(NOW + timedelta(minutes=30), NOW + timedelta(hours=1))
    assert merge_outages([first, inner]) == [first]


def test_no_outages() -> None:
    assert merge_outages([]) == []


# --- Кому и как начислить ---


def test_active_client_gets_absolute_new_expiry() -> None:
    expire_at = NOW + timedelta(days=10)
    plan = plan_compensation(client(expire_at), DAY, already_compensated=False, now=NOW)
    assert plan.action is CompensationAction.EXTEND
    assert plan.extend_to == expire_at + DAY
    assert plan.carry_over is None


def test_same_incident_is_not_compensated_twice() -> None:
    expire_at = NOW + timedelta(days=10)
    plan = plan_compensation(client(expire_at), DAY, already_compensated=True, now=NOW)
    assert plan.action is CompensationAction.ALREADY_COMPENSATED
    assert plan.extend_to is None


def test_grace_client_gets_compensation_carried_over() -> None:
    started = NOW - DAY
    state = client(started + timedelta(days=3), grace_started_at=started)
    amount = timedelta(days=1, hours=4)
    plan = plan_compensation(state, amount, already_compensated=False, now=NOW)
    assert plan.action is CompensationAction.CARRY_OVER
    assert plan.carry_over == amount
    assert plan.extend_to is None  # конец льготного периода не сдвигаем


def test_carried_compensation_is_added_after_payment_in_grace() -> None:
    started = NOW - DAY
    state = client(started + timedelta(days=3), grace_started_at=started)
    amount = timedelta(days=1, hours=4)
    plan = plan_compensation(state, amount, already_compensated=False, now=NOW)
    assert plan.carry_over is not None
    paid_at = NOW + timedelta(hours=6)
    target = grant_target_expire_at(
        state, paid_at, timedelta(days=30), carried_compensation=plan.carry_over
    )
    # Покупка «от момента оплаты» не затирает компенсацию
    assert target == paid_at + timedelta(days=30) + amount


@pytest.mark.parametrize(
    "state",
    [
        client(NOW + timedelta(days=3), on_trial=True),
        client(None),
        client(NOW - timedelta(days=10), access_disabled=True),
        client(NOW - timedelta(hours=1)),
    ],
    ids=["trial", "new", "disabled", "expired"],
)
def test_not_eligible_without_paid_subscription(state: ClientSnapshot) -> None:
    plan = plan_compensation(state, DAY, already_compensated=False, now=NOW)
    assert plan.action is CompensationAction.NOT_ELIGIBLE


def test_compensation_must_be_positive() -> None:
    with pytest.raises(ValueError):
        plan_compensation(client(NOW + DAY), timedelta(0), already_compensated=False, now=NOW)
