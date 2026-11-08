from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import GRACE_DAYS, LAST_CHANCE_DAYS
from tsproxy.domain.grace import (
    GraceAction,
    GraceStage,
    decide_on_expiry,
    grace_ends_at,
    grace_notice,
    is_last_chance,
    observed_grace_extension,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
GRACE = timedelta(days=GRACE_DAYS + LAST_CHANCE_DAYS)


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


def test_grace_is_three_days_plus_last_chance_day() -> None:
    # владелец 2026-10-24: 3 дня с напоминаниями и ещё один «последний» день
    assert grace_ends_at(NOW) == NOW + timedelta(days=4)


def test_last_chance_starts_after_three_days() -> None:
    started = NOW - timedelta(days=3)
    assert is_last_chance(started, NOW)
    assert not is_last_chance(started, NOW - timedelta(seconds=1))


def test_last_chance_ends_with_grace() -> None:
    started = NOW - timedelta(days=4)
    assert not is_last_chance(started, NOW)


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


# --- Какое сообщение о льготном периоде сейчас пора прислать ---


def test_first_day_notice_is_grace_started() -> None:
    notice = grace_notice(NOW, NOW + timedelta(hours=5))
    assert notice.stage is GraceStage.STARTED
    assert notice.due_at == NOW


@pytest.mark.parametrize("day", range(1, GRACE_DAYS))
def test_daily_reminder_on_each_following_grace_day(day: int) -> None:
    notice = grace_notice(NOW, NOW + timedelta(days=day, hours=1))
    assert notice.stage is GraceStage.REMINDER
    assert notice.due_at == NOW + timedelta(days=day)  # своя дата у каждого дня — свой повтор


def test_last_chance_day_notice_is_the_big_warning() -> None:
    notice = grace_notice(NOW, NOW + timedelta(days=GRACE_DAYS))
    assert notice.stage is GraceStage.LAST_DAY
    assert notice.due_at == NOW + timedelta(days=GRACE_DAYS)
    assert is_last_chance(NOW, notice.due_at)


def test_after_grace_notice_is_the_final_one() -> None:
    notice = grace_notice(NOW, NOW + GRACE + timedelta(days=10))
    assert notice.stage is GraceStage.ENDED
    assert notice.due_at == grace_ends_at(NOW)


def test_bot_was_down_for_days_only_current_notice_is_due() -> None:
    # Бот лежал первые два дня: пачку пропущенных не шлём, только сегодняшнее
    notice = grace_notice(NOW, NOW + timedelta(days=2, hours=3))
    assert notice.due_at == NOW + timedelta(days=2)


def test_same_day_gives_same_notice() -> None:
    morning = grace_notice(NOW, NOW + timedelta(days=1, minutes=1))
    evening = grace_notice(NOW, NOW + timedelta(days=1, hours=23))
    assert morning == evening


# --- Льготный, продлённый компенсацией за сбой (задача 20) ---

EXTENSION = timedelta(days=1, hours=3)


def extended(now_offset: timedelta) -> tuple[ClientSnapshot, datetime]:
    """Льготный начат в NOW и продлён на EXTENSION; срок в панели — новый конец."""
    state = ClientSnapshot(
        expire_at=grace_ends_at(NOW, EXTENSION),
        on_trial=False,
        grace_started_at=NOW,
        access_disabled=False,
        grace_extension=EXTENSION,
    )
    return state, NOW + now_offset


def test_extension_moves_grace_end() -> None:
    assert grace_ends_at(NOW, EXTENSION) == NOW + GRACE + EXTENSION


def test_extended_grace_continues_after_usual_end() -> None:
    state, now = extended(GRACE + timedelta(hours=1))
    decision = decide_on_expiry(state, now)
    assert decision.action is GraceAction.ALREADY_IN_GRACE
    assert decision.grace_ends_at == NOW + GRACE + EXTENSION


def test_extended_grace_ends_only_after_new_end() -> None:
    state, now = extended(GRACE + EXTENSION)
    assert decide_on_expiry(state, now).action is GraceAction.END_GRACE


def test_last_day_is_the_day_before_extended_end() -> None:
    usual_last_day = grace_notice(NOW, NOW + timedelta(days=GRACE_DAYS, hours=1), EXTENSION)
    assert usual_last_day.stage is GraceStage.REMINDER  # «последний день» сдвинулся
    last_day_at = NOW + GRACE + EXTENSION - timedelta(days=LAST_CHANCE_DAYS)
    notice = grace_notice(NOW, last_day_at + timedelta(minutes=5), EXTENSION)
    assert notice.stage is GraceStage.LAST_DAY
    assert notice.due_at == last_day_at
    assert is_last_chance(NOW, last_day_at, EXTENSION)


def test_final_notice_at_extended_end() -> None:
    notice = grace_notice(NOW, NOW + GRACE + EXTENSION, EXTENSION)
    assert notice.stage is GraceStage.ENDED
    assert notice.due_at == NOW + GRACE + EXTENSION


def test_extension_cannot_be_negative() -> None:
    with pytest.raises(ValueError):
        grace_ends_at(NOW, timedelta(hours=-1))


def test_observed_extension_is_panel_term_beyond_usual_end() -> None:
    assert observed_grace_extension(NOW, NOW + GRACE + EXTENSION) == EXTENSION
    assert observed_grace_extension(NOW, NOW + GRACE) == timedelta(0)
    # срок в панели ещё не сдвинут до конца льготного — продления нет
    assert observed_grace_extension(NOW, NOW) == timedelta(0)
    assert observed_grace_extension(None, NOW + GRACE + EXTENSION) == timedelta(0)
