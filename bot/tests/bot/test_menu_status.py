"""Строка статуса подписки в главном меню (задача 19 «Статус в меню»).

Срок знает только Remnawave: меню спрашивает панель. Нет прокси-аккаунта, панель
не ответила или думает дольше нескольких секунд — меню без строки и без ошибки.
"""

import asyncio
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from bot_updates import CLIENT_ID, NOW, callback, message, user
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot import menu
from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.models import Client
from tsproxy.domain.constants import BYTES_PER_GB
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    ProxyAccount,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveUnavailable,
)
from tsproxy.services.fakes import FakeAlertSender

MAIN = MenuCallback(screen=ScreenId.MAIN).pack()
# 29.10.2026 15:00 UTC — 18:00 по Москве
EXPIRE_AT = NOW + timedelta(days=5, hours=3)
EXPIRE_AT_TEXT = "29.10.2026, 18:00 МСК"
TRIAL_TRAFFIC = 7 * BYTES_PER_GB


async def start_menu_text(dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession) -> str:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))
    [sent] = telegram.sent(SendMessage)
    return sent.text


async def test_active_subscription_shows_until_date(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=EXPIRE_AT, device_limit=3, traffic_limit_bytes=0
    )

    text = await start_menu_text(dispatcher, bot, telegram)

    assert text.startswith("✅ Активна до <tg-time")  # по часам клиента (задача 22)
    assert EXPIRE_AT_TEXT in text.splitlines()[0]  # запасной текст по Москве
    assert content.texts.get("menu.title") in text


async def test_expired_subscription_shows_ended(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=NOW - timedelta(days=10), device_limit=3, traffic_limit_bytes=0
    )

    text = await start_menu_text(dispatcher, bot, telegram)

    assert text.splitlines()[0] == "⛔ Закончилась"


async def test_trial_shows_until_date(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=CLIENT_ID, first_seen_at=NOW, trial_granted_at=NOW))
        await session.commit()
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=EXPIRE_AT, device_limit=3, traffic_limit_bytes=TRIAL_TRAFFIC
    )

    text = await start_menu_text(dispatcher, bot, telegram)

    assert text.startswith("✅ Пробный период до ")
    assert EXPIRE_AT_TEXT in text.splitlines()[0]


async def test_back_to_menu_shows_status(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
) -> None:
    """«Назад» в меню из любого экрана — тоже со строкой статуса."""
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=EXPIRE_AT, device_limit=3, traffic_limit_bytes=0
    )

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), MAIN))

    [sent] = telegram.sent(SendMessage)
    assert sent.text.startswith("✅ Активна до ")


async def test_no_account_menu_without_status(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    assert await start_menu_text(dispatcher, bot, telegram) == content.texts.get("menu.title")


@pytest.mark.parametrize("error", [RemnawaveUnavailable("нет связи"), RemnawaveAuthError("401")])
async def test_panel_down_menu_without_status_and_error(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
    error: RemnawaveError,
) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=EXPIRE_AT, device_limit=3, traffic_limit_bytes=0
    )
    remnawave.fail_next(error)

    assert await start_menu_text(dispatcher, bot, telegram) == content.texts.get("menu.title")
    assert alert_sender.sent == []


async def test_slow_panel_does_not_hold_menu(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Панель думает дольше нескольких секунд — меню приходит без строки, не дожидаясь."""
    never = asyncio.Event()

    async def hanging_get_account(telegram_id: int) -> ProxyAccount:
        await never.wait()
        raise AssertionError("панель не должна ответить")

    monkeypatch.setattr(remnawave, "get_account", hanging_get_account)
    monkeypatch.setattr(menu, "MENU_STATUS_TIMEOUT_SECONDS", 0.01)

    async with asyncio.timeout(1):
        text = await start_menu_text(dispatcher, bot, telegram)

    assert text == content.texts.get("menu.title")


def test_menu_waits_for_panel_only_a_few_seconds() -> None:
    assert menu.MENU_STATUS_TIMEOUT_SECONDS <= 5
