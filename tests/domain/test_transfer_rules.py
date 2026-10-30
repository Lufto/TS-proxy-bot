"""Перенос подписки на другой Telegram-аккаунт (задача 21): что переносится и куда."""

from datetime import UTC, datetime, timedelta

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.refund import REFUND_END_MARGIN
from tsproxy.domain.transfer import plan_transfer

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def snapshot(
    expire_at: datetime | None,
    *,
    on_trial: bool = False,
    grace_started_at: datetime | None = None,
    access_disabled: bool = False,
) -> ClientSnapshot:
    return ClientSnapshot(expire_at, on_trial, grace_started_at, access_disabled)


def test_remaining_paid_time_moves_to_account_without_subscription() -> None:
    plan = plan_transfer(snapshot(NOW + timedelta(days=20)), snapshot(None), NOW)
    assert plan is not None
    assert plan.left == timedelta(days=20)
    assert plan.target_expire_at == NOW + timedelta(days=20)
    assert plan.source_expire_at == NOW + REFUND_END_MARGIN


def test_remaining_time_is_added_to_running_subscription_of_target() -> None:
    target = snapshot(NOW + timedelta(days=5), on_trial=True)
    plan = plan_transfer(snapshot(NOW + timedelta(days=20)), target, NOW)
    assert plan is not None
    assert plan.target_expire_at == NOW + timedelta(days=25)


def test_expired_target_starts_from_now() -> None:
    target = snapshot(NOW - timedelta(days=5), access_disabled=True)
    plan = plan_transfer(snapshot(NOW + timedelta(days=20)), target, NOW)
    assert plan is not None
    assert plan.target_expire_at == NOW + timedelta(days=20)


def test_nothing_to_move_from_trial_grace_or_expired() -> None:
    later = NOW + timedelta(days=3)
    assert plan_transfer(snapshot(later, on_trial=True), snapshot(None), NOW) is None
    assert plan_transfer(snapshot(later, grace_started_at=NOW), snapshot(None), NOW) is None
    assert plan_transfer(snapshot(later, access_disabled=True), snapshot(None), NOW) is None
    assert plan_transfer(snapshot(NOW - timedelta(seconds=1)), snapshot(None), NOW) is None
    assert plan_transfer(snapshot(None), snapshot(None), NOW) is None
