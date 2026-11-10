"""Пробный период: выдаётся один раз, гонка нажатий не даёт второго, сбой панели не съедает:
пробный за клиентом, и фоновая задача выдаёт его сама, когда панель снова отвечает."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Client, Event, Order
from tsproxy.domain.constants import BYTES_PER_GB, TRIAL_DEVICE_LIMIT, TRIAL_TRAFFIC_GB
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    ProxyAccount,
    RemnawaveAuthError,
    RemnawaveUnavailable,
)
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock, FakeTrialGate
from tsproxy.services.masking import Masker
from tsproxy.services.trial import DeliveredTrial, TrialOutcome, TrialResult, TrialService

TELEGRAM_ID = 111
TRIAL_PERIOD = timedelta(days=7)


class FlakyCreateRemnawave(FakeRemnawave):
    """Панель, которая `create_failures` раз не ответила на создание аккаунта."""

    def __init__(self, now: FakeClock, *, create_failures: int = 1) -> None:
        super().__init__(RunMode.PROD, now=now)
        self.create_failures = create_failures

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
    return TrialService(session_factory, remnawave, EventLog(clock, mask), clock, FakeTrialGate())


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

    assert result == TrialResult(TrialOutcome.ALREADY_USED)
    assert remnawave.writes == [("create", f"tg_{TELEGRAM_ID}")]
    assert len(await trial_events(session)) == 1


async def test_two_simultaneous_presses_give_one_trial(
    trials: TrialService, remnawave: FakeRemnawave, session: AsyncSession, client: Client
) -> None:
    results = await asyncio.gather(
        trials.start(client.id, TELEGRAM_ID), trials.start(client.id, TELEGRAM_ID)
    )

    outcomes = sorted(result.outcome for result in results)
    assert outcomes == [TrialOutcome.GRANTED, TrialOutcome.IN_PROGRESS]
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

    assert result == TrialResult(TrialOutcome.HAS_SUBSCRIPTION, paid)
    assert await remnawave.get_account(TELEGRAM_ID) == paid
    assert await trial_granted_at(session, client) is None
    assert await trial_events(session) == []


async def test_client_who_paid_gets_no_trial_even_if_panel_is_down(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    session.add(
        Order(
            client_id=client.id,
            tariff_id="month",
            price=Decimal(150),
            days=30,
            paid_amount=Decimal(150),
            paid_at=clock.now,
        )
    )
    await session.commit()
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result == TrialResult(TrialOutcome.HAS_SUBSCRIPTION)
    assert await trial_granted_at(session, client) is None


async def test_panel_down_on_lookup_keeps_trial_and_job_delivers_it(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result.outcome is TrialOutcome.PENDING
    assert str(result.error) == "timeout"  # причина — для тревоги
    assert remnawave.accounts == {}
    clock.advance(timedelta(minutes=5))
    [delivered] = await trials.deliver_pending()
    assert delivered == DeliveredTrial(TELEGRAM_ID, await remnawave.get_account(TELEGRAM_ID))
    assert delivered.account.expire_at == clock.now + TRIAL_PERIOD
    assert len(await trial_events(session)) == 1


async def test_token_rejected_keeps_trial_and_job_delivers_it_after_new_token(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    error = RemnawaveAuthError("401")
    remnawave.fail_next(error)

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result.outcome is TrialOutcome.PENDING
    assert result.error is error  # вид сбоя «токен не принят» — для тревоги
    assert remnawave.accounts == {}
    clock.advance(timedelta(minutes=5))
    [delivered] = await trials.deliver_pending()
    assert delivered.telegram_id == TELEGRAM_ID
    assert len(await trial_events(session)) == 1


async def test_job_delivers_trial_once_panel_answers_again(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
    mask: Masker,
) -> None:
    """Панель лежит: нажатие и первые проходы фоновой задачи не выдают пробный, но и не
    теряют его. Панель ожила — задача выдаёт пробный один раз, без нового нажатия."""
    remnawave = FlakyCreateRemnawave(clock, create_failures=2)
    trials = make_service(session_factory, remnawave, clock, mask)

    assert (await trials.start(client.id, TELEGRAM_ID)).outcome is TrialOutcome.PENDING
    assert await trials.deliver_pending() == []  # отметка свежая: выдачу, может, ещё ведут
    clock.advance(timedelta(minutes=3))
    assert await trials.deliver_pending() == []  # панель всё ещё не отвечает
    assert await trial_events(session) == []
    clock.advance(timedelta(minutes=3))

    [delivered] = await trials.deliver_pending()

    assert delivered.account.expire_at == clock.now + TRIAL_PERIOD
    assert len(await trial_events(session)) == 1
    clock.advance(timedelta(minutes=3))
    assert await trials.deliver_pending() == []
    assert await trials.start(client.id, TELEGRAM_ID) == TrialResult(TrialOutcome.ALREADY_USED)


async def test_press_right_after_panel_failure_waits_for_first_press(
    session_factory: async_sessionmaker[AsyncSession],
    client: Client,
    clock: FakeClock,
    mask: Masker,
) -> None:
    """Сразу после сбоя не понять, упало первое нажатие или ещё создаёт аккаунт:
    повтор в первые минуты ничего не создаёт."""
    remnawave = FlakyCreateRemnawave(clock)
    trials = make_service(session_factory, remnawave, clock, mask)
    await trials.start(client.id, TELEGRAM_ID)
    clock.advance(timedelta(seconds=30))

    result = await trials.start(client.id, TELEGRAM_ID)

    assert result == TrialResult(TrialOutcome.IN_PROGRESS)
    assert remnawave.accounts == {}


async def test_account_created_without_answer_is_delivered_by_job(
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    """Панель создала аккаунт, но ответ потерялся: задача не создаёт второй, а записывает
    выдачу и присылает ссылку на уже созданный."""
    session.add(client)
    client.trial_granted_at = clock.now
    await session.commit()
    created = await remnawave.get_or_create_account(
        TELEGRAM_ID,
        expire_at=clock.now + TRIAL_PERIOD,
        device_limit=TRIAL_DEVICE_LIMIT,
        traffic_limit_bytes=TRIAL_TRAFFIC_GB * BYTES_PER_GB,
    )
    clock.advance(timedelta(minutes=5))

    assert await trials.deliver_pending() == [DeliveredTrial(TELEGRAM_ID, created)]
    assert remnawave.writes == [("create", f"tg_{TELEGRAM_ID}")]
    assert len(await trial_events(session)) == 1
