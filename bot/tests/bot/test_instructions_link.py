"""Личная страница подключения в инструкциях (задача 17).

Во фразе «Откройте вашу страницу подключения» — личная ссылка подписки нажавшего.
Нет прокси-аккаунта, панель не ответила или думает дольше нескольких секунд — вместо
ссылки «Нажмите „Подключиться“», ссылка на сайт остаётся, ошибки и тревоги нет.
"""

import asyncio
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from bot_updates import CLIENT_ID, NOW, callback, user

from tsproxy.bot import menu
from tsproxy.bot.callbacks import InstructionsCallback, Platform
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    ProxyAccount,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveUnavailable,
)
from tsproxy.services.fakes import FakeAlertSender

IOS = InstructionsCallback(platform=Platform.IOS).pack()
SITE_LINK = '<a href="https://ts-proxy.com/help/ios/">'
PRESS_CONNECT = "Нажмите «Подключиться»"


async def ios_instructions(dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession) -> str:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), IOS))
    [sent] = telegram.sent(SendMessage)
    return sent.text


async def test_client_with_account_sees_link_to_own_page(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    account = await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=NOW + timedelta(days=5), device_limit=3, traffic_limit_bytes=0
    )

    text = await ios_instructions(dispatcher, bot, telegram)

    assert f'Откройте <a href="{account.subscription_url}">вашу страницу подключения</a>' in text
    assert PRESS_CONNECT not in text
    assert SITE_LINK in text


async def test_client_without_account_is_told_to_press_connect(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    text = await ios_instructions(dispatcher, bot, telegram)

    assert PRESS_CONNECT in text
    assert "вашу страницу подключения</a>" not in text
    assert SITE_LINK in text


@pytest.mark.parametrize("error", [RemnawaveUnavailable("нет связи"), RemnawaveAuthError("401")])
async def test_panel_down_press_connect_without_alert(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    error: RemnawaveError,
) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=NOW + timedelta(days=5), device_limit=3, traffic_limit_bytes=0
    )
    remnawave.fail_next(error)

    text = await ios_instructions(dispatcher, bot, telegram)

    assert PRESS_CONNECT in text
    assert SITE_LINK in text
    assert alert_sender.sent == []


async def test_slow_panel_does_not_hold_instructions(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never = asyncio.Event()

    async def hanging_get_account(telegram_id: int) -> ProxyAccount:
        await never.wait()
        raise AssertionError("панель не должна ответить")

    monkeypatch.setattr(remnawave, "get_account", hanging_get_account)
    monkeypatch.setattr(menu, "INSTRUCTIONS_LINK_TIMEOUT_SECONDS", 0.01)

    async with asyncio.timeout(1):
        text = await ios_instructions(dispatcher, bot, telegram)

    assert PRESS_CONNECT in text
    assert alert_sender.sent == []
