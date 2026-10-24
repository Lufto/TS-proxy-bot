"""Ошибка в обработчике: в лог, в «🚨 Тревоги», клиенту — извинение."""

import logging

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from bot_updates import CLIENT_ID, callback, user

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveAuthError
from tsproxy.services.fakes import FakeAlertSender


async def test_handler_error_is_logged_alerted_and_apologized(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
    caplog: pytest.LogCaptureFixture,
) -> None:
    remnawave.fail_next(RemnawaveAuthError("HTTP 401"))

    with caplog.at_level(logging.ERROR):
        await dispatcher.feed_update(
            bot, callback(user(CLIENT_ID), MenuCallback(screen=ScreenId.CONNECT).pack())
        )

    assert any("RemnawaveAuthError" in r.getMessage() for r in caplog.records)
    [alert] = alert_sender.sent
    assert "RemnawaveAuthError" in alert.text
    [apology] = telegram.sent(SendMessage)
    assert (apology.chat_id, apology.text) == (CLIENT_ID, content.texts.get("errors.generic"))
