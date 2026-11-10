"""Выключатель пробных (задача 28): администратор выключает и включает, бот выключает сам
при спаме; состояние — последнее событие в журнале, поэтому переживает перезапуск."""

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts, load_content
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Event
from tsproxy.domain.constants import TRIAL_SPAM_LIMIT, TRIAL_SPAM_WINDOW
from tsproxy.services.alerts import Alerter
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.trial_switch import TrialSwitch

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
REPO_CONFIG = Path(__file__).parent.parent.parent.parent / "config"
CHAT_ID = -100123
THREAD_ID = 2
ADMIN_ID = 900
SPAM_MINUTES = int(TRIAL_SPAM_WINDOW.total_seconds()) // 60


@pytest.fixture
def admin_texts() -> Texts:
    return load_content(REPO_CONTENT, REPO_CONFIG).admin_texts


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def make_switch(
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    admin_texts: Texts,
    clock: FakeClock,
    mask: Masker,
) -> Callable[[], TrialSwitch]:
    """Новый выключатель на той же базе — как после перезапуска бота."""

    def make() -> TrialSwitch:
        alerter = Alerter(sender, admin_texts, CHAT_ID, THREAD_ID, clock, mask)
        return TrialSwitch(session_factory, EventLog(clock, mask), alerter, clock)

    return make


@pytest.fixture
def switch(make_switch: Callable[[], TrialSwitch]) -> TrialSwitch:
    return make_switch()


async def events_of(
    session_factory: async_sessionmaker[AsyncSession], *kinds: EventKind
) -> list[Event]:
    async with session_factory() as session:
        query = select(Event).where(Event.kind.in_([k.value for k in kinds])).order_by(Event.id)
        return list(await session.scalars(query))


async def test_trials_are_on_when_journal_is_empty(switch: TrialSwitch) -> None:
    assert await switch.enabled()


async def test_admin_disables_trials_with_journal_and_alert(
    switch: TrialSwitch,
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    admin_texts: Texts,
) -> None:
    assert await switch.disable(ADMIN_ID)

    assert not await switch.enabled()
    [state] = await events_of(session_factory, EventKind.TRIALS_DISABLED)
    assert state.admin_id == ADMIN_ID
    [action] = await events_of(session_factory, EventKind.ADMIN_ACTION)
    assert action.admin_id == ADMIN_ID
    assert action.details == {"action": "trials_disabled"}
    [alert] = sender.sent
    assert (alert.chat_id, alert.thread_id) == (CHAT_ID, THREAD_ID)
    assert admin_texts.get("alert.trials_disabled.title") in alert.text
    assert admin_texts.get("alert.trials_disabled.what", admin_id=ADMIN_ID) in alert.text
    assert "TrialSwitch.disable" in alert.text


async def test_second_disable_changes_nothing(
    switch: TrialSwitch,
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
) -> None:
    await switch.disable(ADMIN_ID)

    assert not await switch.disable(ADMIN_ID)

    assert len(await events_of(session_factory, EventKind.TRIALS_DISABLED)) == 1
    assert len(sender.sent) == 1


async def test_enable_when_already_on_changes_nothing(
    switch: TrialSwitch, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert not await switch.enable(ADMIN_ID)

    assert await events_of(session_factory, EventKind.TRIALS_ENABLED) == []


async def test_disabled_survives_restart_and_admin_turns_back_on(
    make_switch: Callable[[], TrialSwitch],
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    admin_texts: Texts,
    clock: FakeClock,
) -> None:
    await make_switch().disable(ADMIN_ID)
    clock.advance(timedelta(hours=1))

    restarted = make_switch()
    assert not await restarted.enabled()

    assert await restarted.enable(ADMIN_ID)
    assert await restarted.enabled()
    assert await make_switch().enabled()
    [*_, alert] = sender.sent
    assert admin_texts.get("alert.trials_enabled.title") in alert.text
    assert admin_texts.get("alert.trials_enabled.what", admin_id=ADMIN_ID) in alert.text
    [_, action_on] = await events_of(session_factory, EventKind.ADMIN_ACTION)
    assert action_on.details == {"action": "trials_enabled"}


async def test_disable_and_enable_alert_each_time(
    switch: TrialSwitch, sender: FakeAlertSender
) -> None:
    """Каждое действие — своё сообщение в «🚨 Тревоги», без склейки."""
    for _ in range(2):
        await switch.disable(ADMIN_ID)
        await switch.enable(ADMIN_ID)

    assert len(sender.sent) == 4


async def test_two_hundred_new_trials_keep_trials_on(
    switch: TrialSwitch, sender: FakeAlertSender, clock: FakeClock
) -> None:
    for _ in range(TRIAL_SPAM_LIMIT):
        await switch.note_new_trial()
        clock.advance(timedelta(seconds=1))

    assert await switch.enabled()
    assert sender.sent == []


async def test_201st_trial_in_ten_minutes_switches_trials_off_once(
    switch: TrialSwitch,
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    admin_texts: Texts,
) -> None:
    for _ in range(TRIAL_SPAM_LIMIT + 5):
        await switch.note_new_trial()

    assert not await switch.enabled()
    [alert] = sender.sent
    assert admin_texts.get("alert.trials_spam.title") in alert.text
    what = admin_texts.get(
        "alert.trials_spam.what", count=TRIAL_SPAM_LIMIT + 1, minutes=SPAM_MINUTES
    )
    assert what in alert.text
    assert "TrialSwitch.note_new_trial" in alert.text
    assert admin_texts.get("alert.trials_spam.causes.1.cause") in alert.text
    [state] = await events_of(session_factory, EventKind.TRIALS_DISABLED)
    assert state.admin_id is None  # выключила система
    assert state.details == {"reason": "spam", "count": TRIAL_SPAM_LIMIT + 1}


async def test_trials_older_than_ten_minutes_do_not_count(
    switch: TrialSwitch, clock: FakeClock
) -> None:
    for _ in range(TRIAL_SPAM_LIMIT):
        await switch.note_new_trial()
    clock.advance(TRIAL_SPAM_WINDOW)

    await switch.note_new_trial()

    assert await switch.enabled()


async def test_auto_switch_off_never_turns_trials_back_on(
    switch: TrialSwitch, clock: FakeClock
) -> None:
    for _ in range(TRIAL_SPAM_LIMIT + 1):
        await switch.note_new_trial()
    clock.advance(timedelta(days=1))

    assert not await switch.enabled()


async def test_admin_turning_trials_on_starts_count_anew(
    switch: TrialSwitch, sender: FakeAlertSender
) -> None:
    """Администратор включил после наплыва — прошлые 201 пробный не выключают их сразу снова."""
    for _ in range(TRIAL_SPAM_LIMIT + 1):
        await switch.note_new_trial()
    await switch.enable(ADMIN_ID)

    await switch.note_new_trial()

    assert await switch.enabled()
