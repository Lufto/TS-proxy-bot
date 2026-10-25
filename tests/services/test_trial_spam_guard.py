"""Пробные и выключатель вместе (задача 28): 201-я выдача за 10 минут выключает пробные,
дальше новых нет; уже взятые пробные и выдача зависших продолжают работать."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.content.loader import Texts, load_content
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Client
from tsproxy.domain.constants import TRIAL_SPAM_LIMIT, TRIAL_SPAM_WINDOW
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    ProxyAccount,
    RemnawaveUnavailable,
)
from tsproxy.services.alerts import Alerter
from tsproxy.services.events import EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.trial import STALE_CLAIM_AFTER, TrialOutcome, TrialService
from tsproxy.services.trial_switch import TrialSwitch

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
ADMIN_ID = 900
FIRST_TELEGRAM_ID = 10_000


class DownRemnawave(FakeRemnawave):
    """Панель, которая не отвечает, пока `down`."""

    down = False

    async def get_account(self, telegram_id: int) -> ProxyAccount:
        if self.down:
            raise RemnawaveUnavailable("timeout")
        return await super().get_account(telegram_id)


@pytest.fixture
def admin_texts() -> Texts:
    return load_content(REPO_CONTENT).admin_texts


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> DownRemnawave:
    return DownRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def switch(
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    admin_texts: Texts,
    clock: FakeClock,
    mask: Masker,
) -> TrialSwitch:
    alerter = Alerter(sender, admin_texts, -100123, 2, clock, mask)
    return TrialSwitch(session_factory, EventLog(clock, mask), alerter, admin_texts, clock)


@pytest.fixture
def trials(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: DownRemnawave,
    switch: TrialSwitch,
    clock: FakeClock,
    mask: Masker,
) -> TrialService:
    return TrialService(session_factory, remnawave, EventLog(clock, mask), clock, switch)


async def new_clients(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, count: int
) -> list[Client]:
    async with session_factory() as session:
        clients = [
            Client(telegram_id=FIRST_TELEGRAM_ID + n, first_seen_at=clock.now) for n in range(count)
        ]
        session.add_all(clients)
        await session.commit()
        return clients


async def has_account(remnawave: FakeRemnawave, telegram_id: int) -> bool:
    try:
        await remnawave.get_account(telegram_id)
    except AccountNotFound:
        return False
    return True


async def test_201st_trial_switches_off_and_202nd_gets_no_account(
    trials: TrialService,
    switch: TrialSwitch,
    remnawave: DownRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    *spam, late = await new_clients(session_factory, clock, TRIAL_SPAM_LIMIT + 2)

    outcomes = [(await trials.start(c.id, c.telegram_id)).outcome for c in spam]

    assert outcomes == [TrialOutcome.GRANTED] * (TRIAL_SPAM_LIMIT + 1)
    assert not await switch.enabled()
    assert len(sender.sent) == 1
    result = await trials.start(late.id, late.telegram_id)
    assert result.outcome is TrialOutcome.DISABLED
    assert not await has_account(remnawave, late.telegram_id)
    async with session_factory() as session:
        assert (await session.get_one(Client, late.id)).trial_granted_at is None


async def test_used_trial_is_still_reported_as_used_when_trials_are_off(
    trials: TrialService,
    switch: TrialSwitch,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    [client] = await new_clients(session_factory, clock, 1)
    await trials.start(client.id, client.telegram_id)
    await switch.disable(ADMIN_ID)

    result = await trials.start(client.id, client.telegram_id)

    assert result.outcome is TrialOutcome.ALREADY_USED


async def test_stuck_trial_is_delivered_while_trials_are_off(
    trials: TrialService,
    switch: TrialSwitch,
    remnawave: DownRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """Пробный взят, пока панель лежала, — он за клиентом и после выключения пробных."""
    first, second = await new_clients(session_factory, clock, 2)
    remnawave.down = True
    assert (await trials.start(first.id, first.telegram_id)).outcome is TrialOutcome.PENDING
    assert (await trials.start(second.id, second.telegram_id)).outcome is TrialOutcome.PENDING
    await switch.disable(ADMIN_ID)
    remnawave.down = False
    clock.advance(STALE_CLAIM_AFTER)

    # клиент нажал старую кнопку сам — выдача доделывается
    result = await trials.start(first.id, first.telegram_id)
    assert result.outcome is TrialOutcome.GRANTED
    # второго доделывает фоновая задача
    [delivered] = await trials.deliver_pending()
    assert delivered.telegram_id == second.telegram_id


async def test_claim_while_panel_is_down_counts_towards_spam(
    trials: TrialService,
    switch: TrialSwitch,
    remnawave: DownRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """Наплыв, пока панель лежит, тоже выключает пробные: отметки — те же нажатия."""
    clients = await new_clients(session_factory, clock, TRIAL_SPAM_LIMIT + 1)
    remnawave.down = True

    for client in clients:
        await trials.start(client.id, client.telegram_id)

    assert not await switch.enabled()


async def test_retaking_stuck_trial_does_not_count_twice(
    trials: TrialService,
    switch: TrialSwitch,
    remnawave: DownRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """Фоновая выдача зависших — не новые нажатия: после долгого сбоя пробные не выключаются."""
    *stuck, extra = await new_clients(session_factory, clock, TRIAL_SPAM_LIMIT + 1)
    remnawave.down = True
    for client in stuck:
        await trials.start(client.id, client.telegram_id)
    remnawave.down = False
    clock.advance(TRIAL_SPAM_WINDOW)

    delivered = 0
    while batch := await trials.deliver_pending():
        delivered += len(batch)
    await trials.start(extra.id, extra.telegram_id)

    assert delivered == TRIAL_SPAM_LIMIT
    assert await switch.enabled()
