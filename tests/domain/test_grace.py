from datetime import UTC, datetime, timedelta

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import GRACE_DAYS
from tsproxy.domain.grace import GraceAction, decide_on_expiry, grace_ends_at

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
GRACE = timedelta(days=GRACE_DAYS)


def paid(
    expire_at: datetime | None,
    *,
    grace_started_at: datetime | None = None,
    access_disabled: bool = False,
    on_trial: bool = False,
) -> ClientSnapshot:
    return ClientSnapshot(
        expire_at=expire_at,
        on_trial=on_trial,
        grace_started_at=grace_started_at,
        access_disabled=access_disabled,
    )


def test_grace_is_three_days() -> None:
    assert grace_ends_at(NOW) == NOW + timedelta(days=3)


def test_paid_subscription_ended_starts_grace_from_expiry() -> None:
    decision = decide_on_expiry(paid(NOW), NOW)
    assert decision.action is GraceAction.START_GRACE
    assert decision.grace_started_at == NOW
    assert decision.grace_ends_at == NOW + GRACE


def test_late_event_starts_grace_from_now_so_client_gets_full_three_days() -> None:
    # Бот лежал 2 часа, сверка нашла окончание только сейчас
    expired_at = NOW - timedelta(hours=2)
    decision = decide_on_expiry(paid(expired_at), NOW)
    assert decision.action is GraceAction.START_GRACE
    assert decision.grace_started_at == NOW
    assert decision.grace_ends_at == NOW + GRACE


def test_double_webhook_does_not_give_six_days() -> None:
    first = decide_on_expiry(paid(NOW), NOW)
    after_first = paid(first.grace_ends_at, grace_started_at=first.grace_started_at)
    second = decide_on_expiry(after_first, NOW + timedelta(seconds=5))
    assert second.action is GraceAction.ALREADY_IN_GRACE
    assert second.grace_ends_at == first.grace_ends_at


def test_double_webhook_before_panel_updated_is_still_already_in_grace() -> None:
    # Отметка уже поставлена, а срок в панели ещё старый
    state = paid(NOW, grace_started_at=NOW)
    assert decide_on_expiry(state, NOW).action is GraceAction.ALREADY_IN_GRACE


def test_grace_ended_disables_access() -> None:
    started = NOW - GRACE
    decision = decide_on_expiry(paid(NOW, grace_started_at=started), NOW)
    assert decision.action is GraceAction.END_GRACE


def test_one_second_before_grace_end_is_still_grace() -> None:
    started = NOW - GRACE + timedelta(seconds=1)
    state = paid(grace_ends_at(started), grace_started_at=started)
    assert decide_on_expiry(state, NOW).action is GraceAction.ALREADY_IN_GRACE


def test_already_disabled_does_nothing_again() -> None:
    state = paid(
        NOW - timedelta(days=5),
        grace_started_at=NOW - timedelta(days=8),
        access_disabled=True,
    )
    assert decide_on_expiry(state, NOW).action is GraceAction.ALREADY_ENDED


def test_stale_event_after_payment_is_ignored() -> None:
    # Клиент уже продлил, а старое событие «истёк» пришло с опозданием
    assert decide_on_expiry(paid(NOW + timedelta(days=30)), NOW).action is GraceAction.NOT_EXPIRED


def test_grace_mark_but_panel_extended_by_admin_does_not_disable() -> None:
    started = NOW - timedelta(days=4)
    state = paid(NOW + timedelta(days=7), grace_started_at=started)
    assert decide_on_expiry(state, NOW).action is GraceAction.NOT_EXPIRED


def test_trial_end_is_not_grace() -> None:
    # После пробного льготного периода нет — это решают правила пробного
    state = paid(NOW, on_trial=True)
    assert decide_on_expiry(state, NOW).action is GraceAction.NOT_PAID_PERIOD


def test_client_without_account_is_not_expired() -> None:
    assert decide_on_expiry(paid(None), NOW).action is GraceAction.NOT_EXPIRED
