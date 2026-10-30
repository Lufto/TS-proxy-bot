from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.compensation import (
    CompensationAction,
    Outage,
    ResumeAction,
    compensation_duration,
    incident_of,
    merge_outages,
    plan_compensation,
    remaining_compensation,
    resume_extension,
)
from tsproxy.domain.grace import grace_ends_at
from tsproxy.domain.subscription import grant_target_expire_at

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def client(
    expire_at: datetime | None,
    *,
    on_trial: bool = False,
    grace_started_at: datetime | None = None,
    access_disabled: bool = False,
    grace_extension: timedelta = timedelta(0),
) -> ClientSnapshot:
    return ClientSnapshot(
        expire_at=expire_at,
        on_trial=on_trial,
        grace_started_at=grace_started_at,
        access_disabled=access_disabled,
        grace_extension=grace_extension,
    )


def grace_target(state: ClientSnapshot, paid_at: datetime) -> datetime:
    return grant_target_expire_at(state, paid_at, timedelta(days=30))


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


def test_same_incident_is_not_compensated_twice() -> None:
    expire_at = NOW + timedelta(days=10)
    plan = plan_compensation(client(expire_at), DAY, already_compensated=True, now=NOW)
    assert plan.action is CompensationAction.ALREADY_COMPENSATED
    assert plan.extend_to is None


def test_grace_client_gets_grace_period_extended() -> None:
    started = NOW - DAY
    state = client(started + timedelta(days=4), grace_started_at=started)
    amount = timedelta(days=1, hours=4)
    plan = plan_compensation(state, amount, already_compensated=False, now=NOW)
    assert plan.action is CompensationAction.EXTEND_GRACE
    assert plan.extend_to == grace_ends_at(started) + amount


def test_second_compensation_in_grace_adds_to_earlier_extension() -> None:
    started = NOW - DAY
    earlier = timedelta(hours=5)
    state = client(
        grace_ends_at(started, earlier), grace_started_at=started, grace_extension=earlier
    )
    plan = plan_compensation(state, DAY, already_compensated=False, now=NOW)
    assert plan.extend_to == grace_ends_at(started) + earlier + DAY


def test_extended_grace_is_still_grace_after_usual_end() -> None:
    started = NOW - timedelta(days=4, hours=1)  # обычный льготный кончился час назад
    extension = timedelta(hours=5)
    state = client(
        grace_ends_at(started, extension), grace_started_at=started, grace_extension=extension
    )
    plan = plan_compensation(state, DAY, already_compensated=False, now=NOW)
    assert plan.action is CompensationAction.EXTEND_GRACE


def test_grace_client_with_term_already_beyond_grace_gets_term_extended() -> None:
    # срок в панели продлили дальше льготного (оплата, администратор), отметку ещё не сняли:
    # продлеваем срок, а не льготный — иначе «до X» укоротил бы подписку
    started = NOW - DAY
    paid_until = NOW + timedelta(days=30)
    state = client(paid_until, grace_started_at=started)
    plan = plan_compensation(state, DAY, already_compensated=False, now=NOW)
    assert plan.action is CompensationAction.EXTEND
    assert plan.extend_to == paid_until + DAY


def test_grace_client_whose_panel_term_lags_gets_grace_end_plus_amount() -> None:
    # льготный начался, а срок в панели ещё не сдвинут (сбой панели) — считаем от конца льготного
    started = NOW - DAY
    state = client(started, grace_started_at=started)
    plan = plan_compensation(state, DAY, already_compensated=False, now=NOW)
    assert plan.action is CompensationAction.EXTEND_GRACE
    assert plan.extend_to == grace_ends_at(started) + DAY


def test_payment_in_grace_does_not_add_compensation() -> None:
    started = NOW - DAY
    extension = timedelta(days=1, hours=4)
    state = client(
        grace_ends_at(started, extension), grace_started_at=started, grace_extension=extension
    )
    paid_at = NOW + timedelta(hours=6)
    # оплата в льготный — от момента оплаты, продление льготного не переносится
    assert grace_target(state, paid_at) == paid_at + timedelta(days=30)


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


# --- Инцидент и уже начисленное (задача 20) ---


def test_incident_of_outage_is_its_merged_group() -> None:
    first = Outage(NOW, NOW + timedelta(minutes=5))
    second = Outage(NOW + timedelta(minutes=20), NOW + timedelta(minutes=30))
    far = Outage(NOW + timedelta(hours=5), NOW + timedelta(hours=6))
    incident = incident_of([first, second, far], second)
    assert incident == Outage(NOW, NOW + timedelta(minutes=30))
    assert incident_of([first, second, far], far) == far


def test_incident_of_outage_missing_from_list_still_counts_it() -> None:
    outage = Outage(NOW, NOW + timedelta(minutes=5))
    assert incident_of([], outage) == outage


def test_nothing_compensated_yet_gives_full_amount() -> None:
    incident = Outage(NOW, NOW + timedelta(hours=2))
    assert remaining_compensation(incident, []) == timedelta(hours=2) + DAY


def test_blink_after_compensation_adds_only_the_new_downtime() -> None:
    # первое падение уже компенсировали (1 час + день), потом нода «мигнула» ещё раз
    first = Outage(NOW, NOW + timedelta(hours=1))
    second = Outage(NOW + timedelta(hours=1, minutes=30), NOW + timedelta(hours=3))
    incident = incident_of([first, second], second)
    assert remaining_compensation(incident, [first]) == timedelta(hours=2)


def test_overlap_already_covered_gives_nothing() -> None:
    # вторая нода упала внутри уже компенсированного простоя первой
    first = Outage(NOW, NOW + timedelta(hours=3))
    inner = Outage(NOW + timedelta(hours=1), NOW + timedelta(hours=2))
    incident = incident_of([first, inner], inner)
    assert remaining_compensation(incident, [first]) == timedelta(0)


def test_bridge_between_two_compensated_incidents_never_goes_negative() -> None:
    # два инцидента уже компенсированы отдельно (по дню сверху), третий их склеил
    first = Outage(NOW, NOW + timedelta(hours=1))
    last = Outage(NOW + timedelta(hours=3), NOW + timedelta(hours=4))
    bridge = Outage(NOW + timedelta(hours=1, minutes=30), NOW + timedelta(hours=2, minutes=30))
    incident = incident_of([first, last, bridge], bridge)
    assert remaining_compensation(incident, [first, last]) == timedelta(0)


# --- Продолжение прерванного начисления ---

BEFORE = NOW + timedelta(days=10)
TARGET = BEFORE + DAY


def test_resume_applies_when_term_is_still_the_old_one() -> None:
    assert resume_extension(BEFORE, BEFORE, TARGET) is ResumeAction.APPLY


def test_resume_sees_applied_term_within_a_second() -> None:
    # панель приняла запрос, но ответ потерялся (таймаут); миллисекунды панели не в счёт
    current = TARGET + timedelta(milliseconds=400)
    assert resume_extension(current, BEFORE, TARGET) is ResumeAction.ALREADY_APPLIED


def test_resume_is_unclear_when_term_changed_otherwise() -> None:
    current = BEFORE + timedelta(days=30)  # например, оплата между сбоем и повтором
    assert resume_extension(current, BEFORE, TARGET) is ResumeAction.UNCLEAR
