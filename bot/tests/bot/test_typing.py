"""«Печатает…»: пока бот ждёт панель или платёжку, у клиента в шапке чата видно действие."""

import asyncio

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendChatAction, SendMessage
from bot_updates import CLIENT_ID, callback, message, user

from tsproxy.bot.callbacks import (
    DeviceCallback,
    InstructionsCallback,
    MenuCallback,
    PaymentCheckCallback,
    Platform,
    RevokeLinkCallback,
    ScreenId,
    StarsCallback,
    TariffCallback,
)
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.services.profile import device_ref

CONNECT = MenuCallback(screen=ScreenId.CONNECT).pack()
MAIN_MENU = MenuCallback(screen=ScreenId.MAIN).pack()
ABOUT = MenuCallback(screen=ScreenId.ABOUT).pack()


def typing_before_answer(telegram: FakeTelegramSession) -> bool:
    """«Печатает…» клиенту ушло раньше ответа (нового экрана)."""
    kinds = [type(r) for r in telegram.requests if isinstance(r, SendChatAction | SendMessage)]
    return kinds[:1] == [SendChatAction] and len(kinds) > 1


PANEL_OR_PAYMENT_BUTTONS = {
    "connect": CONNECT,
    "trial": MenuCallback(screen=ScreenId.TRIAL).pack(),
    "profile": MenuCallback(screen=ScreenId.PROFILE).pack(),
    "devices": MenuCallback(screen=ScreenId.DEVICES).pack(),
    "delete_device": DeviceCallback(ref=device_ref("a")).pack(),
    "revoke_link": RevokeLinkCallback().pack(),
    "pay_sbp": TariffCallback(tariff_id="m1").pack(),
    "pay_stars": StarsCallback(tariff_id="m1").pack(),
    "check_payment": PaymentCheckCallback(order_id=1).pack(),
    "instructions": InstructionsCallback(platform=Platform.IOS).pack(),  # ссылка подписки
    "main_menu": MAIN_MENU,  # строка статуса подписки
}


@pytest.mark.parametrize("data", PANEL_OR_PAYMENT_BUTTONS.values(), ids=PANEL_OR_PAYMENT_BUTTONS)
async def test_button_waiting_for_panel_or_payment_shows_typing(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, data: str
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    assert typing_before_answer(telegram)


async def test_profile_command_shows_typing(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/profile"))

    assert typing_before_answer(telegram)


async def test_start_shows_typing(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """Главное меню ждёт статус подписки от панели."""
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    assert typing_before_answer(telegram)


async def test_screen_without_panel_has_no_typing(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), ABOUT))

    assert telegram.sent(SendChatAction) == []
    assert len(telegram.sent(SendMessage)) == 1


async def test_connect_shows_typing_before_answer(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    [action] = telegram.sent(SendChatAction)
    assert action.chat_id == CLIENT_ID
    assert action.action == ChatAction.TYPING
    assert typing_before_answer(telegram)


async def test_typing_failure_does_not_break_action(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    telegram.fail_next(SendChatAction, TelegramBadRequest, "Bad Request: chat not found")

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    assert len(telegram.sent(SendMessage)) == 1


async def test_slow_typing_does_not_hold_the_action(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """«Печатает…» идёт параллельно с действием: если Telegram долго принимает его,
    экран всё равно приходит, не дожидаясь."""
    telegram.hold(SendChatAction)

    async with asyncio.timeout(1):
        await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    assert len(telegram.sent(SendMessage)) == 1
