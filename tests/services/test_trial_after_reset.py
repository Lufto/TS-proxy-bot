"""Пробный после сброса администратором (задача 18): прокси-аккаунт остаётся с прошлого
пробного, и новое нажатие «Попробовать бесплатно» обновляет его, а не отвечает
«у вас уже есть подписка»."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Client, Event
from tsproxy.domain.constants import BYTES_PER_GB, TRIAL_DEVICE_LIMIT, TRIAL_TRAFFIC_GB
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus, RemnawaveUnavailable
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock, FakeTrialGate
from tsproxy.services.masking import Masker
from tsproxy.services.trial import TrialOutcome, TrialResult, TrialService

TELEGRAM_ID = 111
TRIAL_PERIOD = timedelta(days=7)
TRIAL_TRAFFIC = TRIAL_TRAFFIC_GB * BYTES_PER_GB


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def trials(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
    mask: Masker,
) -> TrialService:
    return TrialService(session_factory, remnawave, EventLog(clock, mask), clock, FakeTrialGate())


async def used_up_trial(
    trials: TrialService, remnawave: FakeRemnawave, client: Client, clock: FakeClock
) -> None:
    """Первый пробный выдан, 7 ГБ израсходованы, срок прошёл и доступ выключен."""
    assert (await trials.start(client.id, TELEGRAM_ID)).outcome is TrialOutcome.GRANTED
    remnawave.set_traffic_used(TELEGRAM_ID, TRIAL_TRAFFIC)
    clock.advance(TRIAL_PERIOD + timedelta(days=3))
    remnawave.set_status(TELEGRAM_ID, AccountStatus.EXPIRED)
    await remnawave.disable_account(TELEGRAM_ID)


async def reset_claim(session: AsyncSession, client: Client) -> None:
    """Так сбрасывает пробный администратор: отметка снята, аккаунт в панели остаётся."""
    await session.execute(
        update(Client)
        .where(Client.id == client.id)
        .values(trial_granted_at=None, trial_extensions=0)
    )
    await session.commit()


async def trial_events(session: AsyncSession) -> list[Event]:
    query = select(Event).where(Event.kind == EventKind.TRIAL_GRANTED.value)
    return list(await session.scalars(query))


async def test_press_after_reset_renews_old_trial_account(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await used_up_trial(trials, remnawave, client, clock)
    await reset_claim(session, client)

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result.outcome is TrialOutcome.GRANTED
    account = await remnawave.get_account(TELEGRAM_ID)
    assert result.account == account
    assert account.status is AccountStatus.ACTIVE
    assert account.expire_at == clock.now + TRIAL_PERIOD
    assert account.traffic_used_bytes == 0
    assert account.traffic_limit_bytes == TRIAL_TRAFFIC
    assert account.device_limit == TRIAL_DEVICE_LIMIT
    assert len(remnawave.accounts) == 1
    assert len(await trial_events(session)) == 2


async def test_second_press_after_renewal_is_already_used(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await used_up_trial(trials, remnawave, client, clock)
    await reset_claim(session, client)
    await trials.start(client.id, TELEGRAM_ID)
    clock.advance(timedelta(minutes=1))

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result == TrialResult(TrialOutcome.ALREADY_USED)


async def test_paid_account_after_reset_still_has_subscription(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    """Лимит трафика снят (подписку выдали) — пробный поверх неё не выдаётся."""
    await trials.start(client.id, TELEGRAM_ID)
    paid = await remnawave.set_limits(TELEGRAM_ID, device_limit=3, traffic_limit_bytes=0)
    await reset_claim(session, client)

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result == TrialResult(TrialOutcome.HAS_SUBSCRIPTION, paid)
    assert len(await trial_events(session)) == 1


async def test_renewal_interrupted_by_panel_is_finished_by_job(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await used_up_trial(trials, remnawave, client, clock)
    await reset_claim(session, client)
    original_set_expiry = remnawave.set_expiry

    async def failing_set_expiry(*args: object, **kwargs: object) -> object:
        raise RemnawaveUnavailable("timeout")

    remnawave.set_expiry = failing_set_expiry  # type: ignore[method-assign]
    assert (await trials.start(client.id, TELEGRAM_ID)).outcome is TrialOutcome.PENDING
    remnawave.set_expiry = original_set_expiry  # type: ignore[method-assign]
    clock.advance(timedelta(minutes=5))

    [delivered] = await trials.deliver_pending()

    assert delivered.account.status is AccountStatus.ACTIVE
    assert delivered.account.expire_at == clock.now + TRIAL_PERIOD
    assert len(await trial_events(session)) == 2
