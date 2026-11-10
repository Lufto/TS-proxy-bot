"""Проверка панели при запуске: тревога и при «не ответила», и при «не пустила» — без сети."""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from aiogram.methods import SendMessage
from bot_updates import ADMIN_CHAT_ID
from test_app import make_settings

from tsproxy.app import Application, build_application, check_panel, close_application
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.integrations.failures import Failure
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveFailure,
    RemnawaveUnavailable,
    UnexpectedPanelVersion,
)

PANEL_TIMEOUT = RemnawaveUnavailable(
    "Панель не ответила: ReadTimeout",
    failure=Failure(RemnawaveFailure.TIMEOUT, {"seconds": 10, "attempts": 3}),
)
TOKEN_REJECTED = RemnawaveAuthError(
    "Панель не приняла токен: HTTP 401",
    failure=Failure(RemnawaveFailure.TOKEN_REJECTED, {"status": 401}),
)
WRONG_VERSION = UnexpectedPanelVersion(
    "Панель 4.0.0, а бот написан под 3.x",
    failure=Failure(RemnawaveFailure.UNEXPECTED_VERSION, {"version": "4.0.0", "expected": "3.x"}),
)


@pytest.fixture
def telegram() -> FakeTelegramSession:
    return FakeTelegramSession()


@pytest.fixture
def remnawave() -> FakeRemnawave:
    return FakeRemnawave()


@pytest.fixture
async def app(
    tmp_path: Path, content: Content, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> AsyncIterator[Application]:
    built = build_application(make_settings(tmp_path), content, telegram_session=telegram)
    yield dataclasses.replace(built, remnawave=remnawave)
    await close_application(built)


async def test_panel_down_at_start_alerts_and_starts(
    app: Application, remnawave: FakeRemnawave, telegram: FakeTelegramSession, content: Content
) -> None:
    remnawave.fail_next(PANEL_TIMEOUT)

    await check_panel(app, asyncio.Event())

    [alert] = telegram.sent(SendMessage)
    texts = content.admin_texts
    assert alert.chat_id == ADMIN_CHAT_ID
    assert texts.get("alert.remnawave_at_start.title") in alert.text
    assert "10 сек" in alert.text
    assert "tsproxy.app.check_panel" in alert.text
    assert texts.get("failure.remnawave.timeout.causes.1.cause") in alert.text


@pytest.mark.parametrize(
    ("error", "kind"),
    [(TOKEN_REJECTED, "token_rejected"), (WRONG_VERSION, "unexpected_version")],
)
async def test_panel_refusing_bot_alerts_then_stops_start(
    app: Application,
    remnawave: FakeRemnawave,
    telegram: FakeTelegramSession,
    content: Content,
    error: RemnawaveError,
    kind: str,
) -> None:
    remnawave.fail_next(error)

    with pytest.raises(type(error)):
        await check_panel(app, asyncio.Event(), pause=timedelta(0))

    [alert] = telegram.sent(SendMessage)
    texts = content.admin_texts
    assert texts.get("alert.remnawave_start_refused.title") in alert.text
    assert str(error) in alert.text
    assert texts.get(f"failure.remnawave.{kind}.causes.1.cause") in alert.text


async def test_stop_signal_cuts_the_pause_before_exit(
    app: Application, remnawave: FakeRemnawave
) -> None:
    remnawave.fail_next(TOKEN_REJECTED)
    stop = asyncio.Event()
    stop.set()

    with pytest.raises(RemnawaveAuthError):
        await asyncio.wait_for(check_panel(app, stop, pause=timedelta(hours=1)), timeout=5)
