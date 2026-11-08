"""Обработчики, меняющие срок (льготный, продление пробного), ждут общую блокировку срока
и решают по свежему аккаунту: оплата, засчитанная за это время, не затирается."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Client
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import ProxyAccount
from tsproxy.services.account_checks import AccountCheck, CheckSource
from tsproxy.services.fakes import FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.term_guard import TermLockedHandlers

TELEGRAM_ID = 111


class RecordingHandler:
    def __init__(self) -> None:
        self.seen: list[ProxyAccount] = []

    async def handle(self, check: AccountCheck) -> None:
        self.seen.append(check.account)


class FailingHandler:
    async def handle(self, check: AccountCheck) -> None:
        raise RuntimeError("сломался")


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


def check_of(account: ProxyAccount, clock: FakeClock) -> AccountCheck:
    return AccountCheck(TELEGRAM_ID, account, CheckSource.RECONCILIATION, None, clock.now)


async def test_handlers_wait_for_payment_and_see_fresh_expiry(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    client: Client,
    clock: FakeClock,
) -> None:
    stale = await remnawave.get_or_create_account(
        TELEGRAM_ID, expire_at=clock.now + timedelta(hours=1), device_limit=3, traffic_limit_bytes=0
    )
    locks = KeyedLocks()
    inner = RecordingHandler()
    guarded = TermLockedHandlers([inner], session_factory, remnawave, locks)
    paid_until = clock.now + timedelta(days=30)

    async with locks.hold(client.id):  # «зачёт оплаты» держит блокировку
        task = asyncio.create_task(guarded.handle(check_of(stale, clock)))
        await asyncio.sleep(0)
        assert inner.seen == []
        await remnawave.set_expiry(TELEGRAM_ID, paid_until)
    await task

    [seen] = inner.seen
    assert seen.expire_at == paid_until


async def test_one_failing_handler_does_not_skip_the_others(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    client: Client,
    clock: FakeClock,
) -> None:
    account = await remnawave.get_or_create_account(
        TELEGRAM_ID, expire_at=clock.now + timedelta(days=1), device_limit=3, traffic_limit_bytes=0
    )
    after = RecordingHandler()
    guarded = TermLockedHandlers(
        [FailingHandler(), after], session_factory, remnawave, KeyedLocks()
    )

    with pytest.raises(ExceptionGroup):
        await guarded.handle(check_of(account, clock))
    assert len(after.seen) == 1


async def test_client_missing_in_db_is_skipped(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    account = await remnawave.get_or_create_account(
        TELEGRAM_ID, expire_at=clock.now + timedelta(days=1), device_limit=3, traffic_limit_bytes=0
    )
    inner = RecordingHandler()
    guarded = TermLockedHandlers([inner], session_factory, remnawave, KeyedLocks())

    await guarded.handle(check_of(account, clock))

    assert inner.seen == []
