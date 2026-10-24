"""Пробный период: выдаётся один раз, гонка двух нажатий не даёт второго, сбой панели не съедает."""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Client, Event
from tsproxy.domain.constants import BYTES_PER_GB, TRIAL_DEVICE_LIMIT, TRIAL_TRAFFIC_GB
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import ProxyAccount, RemnawaveUnavailable
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.trial import TrialOutcome, TrialResult, TrialService

TELEGRAM_ID = 111
TRIAL_PERIOD = timedelta(days=7)


class FlakyCreateRemnawave(FakeRemnawave):
    """Панель, которая один раз не ответила на создание аккаунта."""

    def __init__(self, now: FakeClock) -> None:
        super().__init__(RunMode.PROD, now=now)
        self.create_failures = 1

    async def get_or_create_account(
        self,
        telegram_id: int,
        *,
        expire_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
    ) -> ProxyAccount:
        if self.create_failures > 0:
            self.create_failures -= 1
            raise RemnawaveUnavailable("timeout")
        return await super().get_or_create_account(
            telegram_id,
            expire_at=expire_at,
            device_limit=device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
        )


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


def make_service(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
    mask: Masker,
) -> TrialService:
    return TrialService(session_factory, remnawave, EventLog(clock, mask), clock)


@pytest.fixture
def trials(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
    mask: Masker,
) -> TrialService:
    return make_service(session_factory, remnawave, clock, mask)


async def trial_granted_at(session: AsyncSession, client: Client) -> datetime | None:
    await session.refresh(client)
    return client.trial_granted_at


async def trial_events(session: AsyncSession) -> list[Event]:
    query = select(Event).where(Event.kind == EventKind.TRIAL_GRANTED.value)
    return list(await session.scalars(query))


async def test_first_press_creates_trial_account(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    result = await trials.start(client.id, TELEGRAM_ID)

    assert result.outcome is TrialOutcome.GRANTED
    account = await remnawave.get_account(TELEGRAM_ID)
    assert result.account == account
    assert account.expire_at == clock.now + TRIAL_PERIOD
    assert account.traffic_limit_bytes == TRIAL_TRAFFIC_GB * BYTES_PER_GB
    assert account.device_limit == TRIAL_DEVICE_LIMIT
    assert await trial_granted_at(session, client) == clock.now
    [event] = await trial_events(session)
    assert event.client_id == client.id


async def test_second_press_gives_no_second_trial(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await trials.start(client.id, TELEGRAM_ID)
    clock.advance(timedelta(days=3))

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result.outcome is TrialOutcome.ALREADY_USED
    assert result.account == await remnawave.get_account(TELEGRAM_ID)
    assert remnawave.writes == [("create", f"tg_{TELEGRAM_ID}")]
    assert len(await trial_events(session)) == 1


async def test_two_simultaneous_presses_give_one_trial(
    trials: TrialService, remnawave: FakeRemnawave, session: AsyncSession, client: Client
) -> None:
    results = await asyncio.gather(
        trials.start(client.id, TELEGRAM_ID), trials.start(client.id, TELEGRAM_ID)
    )

    outcomes = sorted(result.outcome for result in results)
    assert outcomes == [TrialOutcome.ALREADY_USED, TrialOutcome.GRANTED]
    assert remnawave.writes == [("create", f"tg_{TELEGRAM_ID}")]
    assert len(await trial_events(session)) == 1


async def test_client_with_subscription_gets_no_trial(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    paid_until = clock.now + timedelta(days=30)
    paid = await remnawave.get_or_create_account(
        TELEGRAM_ID, expire_at=paid_until, device_limit=3, traffic_limit_bytes=0
    )

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result.outcome is TrialOutcome.HAS_SUBSCRIPTION
    assert result.account == paid
    assert await remnawave.get_account(TELEGRAM_ID) == paid
    assert await trial_granted_at(session, client) is None
    assert await trial_events(session) == []


async def test_panel_down_on_lookup_claims_nothing(
    trials: TrialService, remnawave: FakeRemnawave, session: AsyncSession, client: Client
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    with pytest.raises(RemnawaveUnavailable):
        await trials.start(client.id, TELEGRAM_ID)

    assert await trial_granted_at(session, client) is None
    assert remnawave.accounts == {}


async def test_panel_down_on_create_keeps_trial_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
    mask: Masker,
) -> None:
    """Пробный отмечен, а аккаунт не создан: следующее нажатие доделывает выдачу,
    и клиент получает полные 7 дней от момента, когда доступ действительно появился."""
    remnawave = FlakyCreateRemnawave(clock)
    trials = make_service(session_factory, remnawave, clock, mask)

    with pytest.raises(RemnawaveUnavailable):
        await trials.start(client.id, TELEGRAM_ID)
    assert await trial_events(session) == []
    clock.advance(timedelta(hours=2))

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result.outcome is TrialOutcome.GRANTED
    assert result.account is not None
    assert result.account.expire_at == clock.now + TRIAL_PERIOD
    assert len(await trial_events(session)) == 1


async def test_retry_right_after_panel_failure_waits_for_first_press(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
    mask: Masker,
) -> None:
    """Сразу после сбоя не понять, упало первое нажатие или ещё создаёт аккаунт:
    повтор в первые минуты ничего не создаёт."""
    remnawave = FlakyCreateRemnawave(clock)
    trials = make_service(session_factory, remnawave, clock, mask)
    with pytest.raises(RemnawaveUnavailable):
        await trials.start(client.id, TELEGRAM_ID)
    clock.advance(timedelta(seconds=30))

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result == TrialResult(TrialOutcome.ALREADY_USED, None)
    assert remnawave.accounts == {}
