"""Объявление в приложении (задача 35): поставить, снять, показать; каждое действие — в журнал."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Event
from tsproxy.domain.announcement import (
    ANNOUNCE_MAX_LENGTH,
    AnnouncementProblem,
    encode_announcement,
)
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.announcement import AnnouncementActionKind, AnnouncementService
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker

ADMIN_ID = 900
TEXT = "Плановые работы 1 ноября с 3:00 до 4:00 МСК"
PROFILE_HEADERS = {"profile-title": "TS Proxy", "support-url": "https://t.me/ts_proxy_support"}


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    fake = FakeRemnawave(now=clock)
    fake.subscription_headers.update(PROFILE_HEADERS)
    return fake


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
    mask: Masker,
) -> AnnouncementService:
    return AnnouncementService(session_factory, remnawave, EventLog(clock, mask))


async def admin_events(session_factory: async_sessionmaker[AsyncSession]) -> list[Event]:
    async with session_factory() as session:
        query = select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value).order_by(Event.id)
        return list(await session.scalars(query))


async def test_nothing_is_shown_at_start(service: AnnouncementService) -> None:
    assert await service.current() is None


async def test_publish_puts_encoded_text_and_keeps_other_headers(
    service: AnnouncementService,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await service.publish(ADMIN_ID, f"  {TEXT}\n") is None

    assert remnawave.subscription_headers == {
        **PROFILE_HEADERS,
        "announce": encode_announcement(TEXT),
    }
    assert await service.current() == TEXT
    [event] = await admin_events(session_factory)
    assert event.admin_id == ADMIN_ID
    assert event.details == {
        "action": AnnouncementActionKind.SET.value,
        "text": TEXT,
        "previous": None,
    }


async def test_publish_over_previous_records_what_was_replaced(
    service: AnnouncementService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await service.publish(ADMIN_ID, "Старое")
    await service.publish(ADMIN_ID, TEXT)

    assert await service.current() == TEXT
    events = await admin_events(session_factory)
    assert events[-1].details == {
        "action": AnnouncementActionKind.SET.value,
        "text": TEXT,
        "previous": "Старое",
    }


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("  ", AnnouncementProblem.EMPTY),
        ("я" * (ANNOUNCE_MAX_LENGTH + 1), AnnouncementProblem.TOO_LONG),
    ],
)
async def test_unfit_text_changes_nothing(
    service: AnnouncementService,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    text: str,
    problem: AnnouncementProblem,
) -> None:
    assert await service.publish(ADMIN_ID, text) is problem

    assert remnawave.writes == []
    assert await admin_events(session_factory) == []


async def test_withdraw_removes_only_announce_and_records_text(
    service: AnnouncementService,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await service.publish(ADMIN_ID, TEXT)

    assert await service.withdraw(ADMIN_ID + 1) == TEXT

    assert remnawave.subscription_headers == PROFILE_HEADERS
    assert await service.current() is None
    events = await admin_events(session_factory)
    assert events[-1].admin_id == ADMIN_ID + 1
    assert events[-1].details == {"action": AnnouncementActionKind.CLEARED.value, "text": TEXT}


async def test_withdraw_when_nothing_is_set(
    service: AnnouncementService,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await service.withdraw(ADMIN_ID) is None

    assert remnawave.writes == []
    assert await admin_events(session_factory) == []


async def test_text_set_in_panel_by_hand_is_shown_and_withdrawn(
    service: AnnouncementService, remnawave: FakeRemnawave
) -> None:
    remnawave.subscription_headers["announce"] = "Maintenance tonight"

    assert await service.current() == "Maintenance tonight"
    assert await service.withdraw(ADMIN_ID) == "Maintenance tonight"


async def test_panel_outage_leaves_no_journal_record(
    service: AnnouncementService,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("panel down"))

    with pytest.raises(RemnawaveUnavailable):
        await service.publish(ADMIN_ID, TEXT)

    assert remnawave.subscription_headers == PROFILE_HEADERS
    assert await admin_events(session_factory) == []
