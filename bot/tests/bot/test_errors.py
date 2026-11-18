"""Ошибка в обработчике: в лог, в «🚨 Тревоги» (что, где, что делали), клиенту — извинение."""

import logging

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from bot_updates import BOT_TOKEN, CLIENT_ID, callback, message, user

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.delivery import KEPT_TEXT_MARK
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveRejected, RemnawaveResponseError
from tsproxy.services.fakes import FakeAlertSender

CONNECT = MenuCallback(screen=ScreenId.CONNECT).pack()


def rejected_with_secret() -> RemnawaveRejected:
    """Ошибка без вида сбоя, в сообщении которой оказался секрет."""
    return RemnawaveRejected(f"HTTP 400, токен {BOT_TOKEN}", status_code=400, code=None)


async def test_handler_error_is_logged_alerted_and_apologized(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
    caplog: pytest.LogCaptureFixture,
) -> None:
    remnawave.fail_next(rejected_with_secret())

    with caplog.at_level(logging.ERROR):
        await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    assert any("RemnawaveRejected" in r.getMessage() for r in caplog.records)
    [alert] = alert_sender.sent
    texts = content.admin_texts
    assert texts.get("alert.bot_error.title") in alert.text
    assert "RemnawaveRejected: HTTP 400" in alert.text
    assert BOT_TOKEN not in alert.text
    assert (
        texts.get(
            "bot_error_action.button",
            button="menu",
            chat=texts.get("bot_error_action.chat.private"),
        )
        in alert.text
    )
    assert "<code>tsproxy." in alert.text  # место в нашем коде: модуль.функция:строка
    assert texts.get("alert.bot_error.causes.1.cause") in alert.text
    [apology] = telegram.sent(SendMessage)
    assert (apology.chat_id, apology.text) == (CLIENT_ID, content.texts.get("errors.generic"))


async def test_button_of_kept_text_is_named_without_mark(
    dispatcher: Dispatcher,
    bot: Bot,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    """Кнопка уведомления несёт метку сохраняемого текста — в тревоге вид кнопки без неё."""
    remnawave.fail_next(rejected_with_secret())

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), KEPT_TEXT_MARK + CONNECT))

    [alert] = alert_sender.sent
    texts = content.admin_texts
    private = texts.get("bot_error_action.chat.private")
    assert texts.get("bot_error_action.button", button="menu", chat=private) in alert.text


async def test_command_is_named(
    dispatcher: Dispatcher,
    bot: Bot,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    remnawave.fail_next(rejected_with_secret())

    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/profile"))

    [alert] = alert_sender.sent
    texts = content.admin_texts
    private = texts.get("bot_error_action.chat.private")
    assert texts.get("bot_error_action.command", command="/profile", chat=private) in alert.text


async def test_error_with_failure_kind_points_to_its_causes(
    dispatcher: Dispatcher,
    bot: Bot,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    remnawave.fail_next(RemnawaveResponseError("ответ панели не той формы: 2 поля"))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    [alert] = alert_sender.sent
    assert "2 поля" in alert.text
    cause = content.admin_texts.get("failure.remnawave.bad_response.causes.1.cause")
    assert cause in alert.text
