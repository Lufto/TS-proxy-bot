"""Клиент сам перевыпускает ссылку подписки (задача 31): новая ссылка, событие в журнале,
не чаще раза в сутки, двойное «Да» — один перевыпуск."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Client, Event
from tsproxy.domain.constants import PAID_DEVICE_LIMIT, UNLIMITED_TRAFFIC_BYTES
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.admin import AdminService
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.link_revoke import (
    REVOKED_BY_CLIENT,
    LinkRevoked,
    LinkRevokeNoAccount,
    LinkRevokeService,
    LinkRevokeTooSoon,
)
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.profile import ProfileService

TELEGRAM_ID = 111  # клиент из фикстуры `client`
OTHER_TELEGRAM_ID = 222
ADMIN_ID = 900


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def events(clock: FakeClock, mask: Masker) -> EventLog:
    return EventLog(clock, mask)


@pytest.fixture
def revokes(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    events: EventLog,
    clock: FakeClock,
) -> LinkRevokeService:
    return LinkRevokeService(session_factory, remnawave, events, clock, KeyedLocks())


async def create_account(remnawave: FakeRemnawave, clock: FakeClock, telegram_id: int) -> str:
    """Платный аккаунт; возвращает нынешнюю ссылку подписки."""
    account = await remnawave.get_or_create_account(
        telegram_id,
        expire_at=clock.now + timedelta(days=30),
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )
    return account.subscription_url


async def revoke_events(session: AsyncSession) -> list[Event]:
    query = select(Event).where(Event.kind == EventKind.SUBSCRIPTION_REVOKED.value)
    return list(await session.scalars(query.order_by(Event.id)))


def revoke_writes(remnawave: FakeRemnawave) -> int:
    return sum(1 for operation, _ in remnawave.writes if operation == "revoke")


async def test_revoke_gives_new_link_and_records_event(
    revokes: LinkRevokeService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    old_url = await create_account(remnawave, clock, TELEGRAM_ID)

    result = await revokes.revoke(client)

    assert isinstance(result, LinkRevoked)
    assert result.account.subscription_url != old_url
    assert (await remnawave.get_account(TELEGRAM_ID)).subscription_url == (
        result.account.subscription_url
    )
    [event] = await revoke_events(session)
    assert (event.client_id, event.admin_id, event.occurred_at) == (client.id, None, clock.now)
    assert event.details == {"by": REVOKED_BY_CLIENT}


async def test_second_revoke_within_a_day_is_refused_with_date(
    revokes: LinkRevokeService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await create_account(remnawave, clock, TELEGRAM_ID)
    first_at = clock.now
    await revokes.revoke(client)
    url_after_first = (await remnawave.get_account(TELEGRAM_ID)).subscription_url
    clock.advance(timedelta(hours=23, minutes=59))

    result = await revokes.revoke(client)

    assert result == LinkRevokeTooSoon(available_at=first_at + timedelta(days=1))
    assert (await remnawave.get_account(TELEGRAM_ID)).subscription_url == url_after_first
    assert revoke_writes(remnawave) == 1
    assert len(await revoke_events(session)) == 1


async def test_revoke_allowed_again_after_a_day(
    revokes: LinkRevokeService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await create_account(remnawave, clock, TELEGRAM_ID)
    await revokes.revoke(client)
    url_after_first = (await remnawave.get_account(TELEGRAM_ID)).subscription_url
    clock.advance(timedelta(days=1))

    result = await revokes.revoke(client)

    assert isinstance(result, LinkRevoked)
    assert result.account.subscription_url != url_after_first
    assert len(await revoke_events(session)) == 2


async def test_double_yes_revokes_once(
    revokes: LinkRevokeService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    """Два «Да» почти одновременно: второе ждёт первое и видит, что ссылку уже перевыпустили."""
    await create_account(remnawave, clock, TELEGRAM_ID)

    results = await asyncio.gather(revokes.revoke(client), revokes.revoke(client))

    assert sorted(type(r).__name__ for r in results) == ["LinkRevokeTooSoon", "LinkRevoked"]
    assert revoke_writes(remnawave) == 1
    assert len(await revoke_events(session)) == 1


async def test_without_account_nothing_to_revoke(
    revokes: LinkRevokeService, remnawave: FakeRemnawave, session: AsyncSession, client: Client
) -> None:
    assert await revokes.revoke(client) == LinkRevokeNoAccount()
    assert await revoke_events(session) == []
    assert remnawave.writes == []


async def test_panel_failure_is_not_hidden_and_not_counted(
    revokes: LinkRevokeService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await create_account(remnawave, clock, TELEGRAM_ID)
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    with pytest.raises(RemnawaveUnavailable):
        await revokes.revoke(client)

    assert await revoke_events(session) == []
    # Сбой не съедает попытку: клиент может нажать ещё раз
    assert isinstance(await revokes.revoke(client), LinkRevoked)


async def test_other_clients_revoke_does_not_count(
    revokes: LinkRevokeService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    other = Client(telegram_id=OTHER_TELEGRAM_ID, first_seen_at=clock.now)
    session.add(other)
    await session.commit()
    await create_account(remnawave, clock, TELEGRAM_ID)
    await create_account(remnawave, clock, OTHER_TELEGRAM_ID)

    assert isinstance(await revokes.revoke(other), LinkRevoked)
    assert isinstance(await revokes.revoke(client), LinkRevoked)


async def test_admin_revoke_does_not_use_up_client_attempt(
    revokes: LinkRevokeService,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    events: EventLog,
    client: Client,
    clock: FakeClock,
) -> None:
    """Администратор перевыпускает по просьбе клиента — это не попытка самого клиента."""
    await create_account(remnawave, clock, TELEGRAM_ID)
    admin = AdminService(
        session_factory, remnawave, ProfileService(remnawave, clock), events, clock, KeyedLocks()
    )
    await admin.revoke_link(ADMIN_ID, TELEGRAM_ID)

    assert await revokes.available_at(client) is None
    assert isinstance(await revokes.revoke(client), LinkRevoked)


async def test_available_at_tells_when_next_revoke_is_possible(
    revokes: LinkRevokeService, remnawave: FakeRemnawave, client: Client, clock: FakeClock
) -> None:
    await create_account(remnawave, clock, TELEGRAM_ID)
    assert await revokes.available_at(client) is None

    await revokes.revoke(client)

    assert await revokes.available_at(client) == clock.now + timedelta(days=1)
