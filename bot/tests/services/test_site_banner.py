"""Плашка на сайте (задачи сайта 14, 15): администратор ставит и снимает, состояние —
последнее событие в журнале, поэтому переживает перезапуск; сама не снимается."""

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts, load_content
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Event
from tsproxy.domain.site_banner import SiteBanner
from tsproxy.services.alerts import Alerter
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.site_banner import SiteBannerSwitch

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
REPO_CONFIG = Path(__file__).parent.parent.parent.parent / "config"
CHAT_ID = -100123
THREAD_ID = 2
ADMIN_ID = 900


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
) -> Callable[[], SiteBannerSwitch]:
    """Новая плашка на той же базе — как после перезапуска бота."""

    def make() -> SiteBannerSwitch:
        alerter = Alerter(sender, admin_texts, CHAT_ID, THREAD_ID, clock, mask)
        return SiteBannerSwitch(session_factory, EventLog(clock, mask), alerter, admin_texts)

    return make


async def test_no_banner_by_default(make_switch: Callable[[], SiteBannerSwitch]) -> None:
    assert await make_switch().current() is SiteBanner.NONE


async def test_admin_sets_banner_and_other_admins_learn_it(
    make_switch: Callable[[], SiteBannerSwitch],
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    admin_texts: Texts,
) -> None:
    switch = make_switch()

    assert await switch.set(SiteBanner.MAINTENANCE, ADMIN_ID)

    assert await switch.current() is SiteBanner.MAINTENANCE
    [alert] = sender.sent
    assert alert.text == admin_texts.get("alerts.prefix") + admin_texts.get(
        "alerts.site_banner_set",
        banner=admin_texts.get("admin.site_banner.names.maintenance"),
        admin_id=ADMIN_ID,
    )
    async with session_factory() as session:
        kinds = list(await session.scalars(select(Event.kind).order_by(Event.id)))
    assert kinds == [EventKind.SITE_BANNER_SET.value, EventKind.ADMIN_ACTION.value]


async def test_same_banner_twice_changes_nothing(
    make_switch: Callable[[], SiteBannerSwitch], sender: FakeAlertSender
) -> None:
    switch = make_switch()
    await switch.set(SiteBanner.PROXY_ISSUES, ADMIN_ID)

    assert not await switch.set(SiteBanner.PROXY_ISSUES, ADMIN_ID)
    assert len(sender.sent) == 1


async def test_banner_survives_restart_and_stays_until_removed(
    make_switch: Callable[[], SiteBannerSwitch], clock: FakeClock
) -> None:
    await make_switch().set(SiteBanner.PROXY_ISSUES, ADMIN_ID)
    clock.advance(timedelta(days=3))

    restarted = make_switch()
    assert await restarted.current() is SiteBanner.PROXY_ISSUES

    await restarted.set(SiteBanner.NONE, ADMIN_ID)
    assert await make_switch().current() is SiteBanner.NONE
