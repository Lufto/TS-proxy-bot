"""Живое меню: `/start` и экраны не по нажатию убирают прошлое меню (задача 24).

Бот помнит последнее живое меню клиента в памяти; после перезапуска память пуста,
и старое меню убирается при нажатии на нём.
"""

from dataclasses import replace
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import DeleteMessage, EditMessageReplyMarkup, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, InaccessibleMessage, Update
from bot_updates import CLIENT_ID, MENU_MESSAGE_ID, callback, message, private_chat, user

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.dispatcher import create_dispatcher
from tsproxy.bot.fakes import FIRST_MESSAGE_ID, FakeTelegramSession
from tsproxy.bot.pictures import TelegramPictures
from tsproxy.bot.throttling import RateLimiter
from tsproxy.content.loader import Content
from tsproxy.services.clients import ClientService
from tsproxy.services.fakes import FakeAlertSender

# Подделка Telegram нумерует отправленные сообщения подряд
FIRST_SENT_ID = FIRST_MESSAGE_ID + 1


async def test_second_start_removes_previous_menu(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    _, second = telegram.sent(SendPhoto)  # главное меню — фото
    [deleted] = telegram.sent(DeleteMessage)
    assert (deleted.chat_id, deleted.message_id) == (CLIENT_ID, FIRST_SENT_ID)
    assert telegram.requests.index(second) < telegram.requests.index(deleted)  # сначала новое


async def test_start_removes_screen_opened_by_press(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """Нажатие убирает нажатое меню, `/start` — экран, пришедший на нажатие: в чате одно меню."""
    about = MenuCallback(screen=ScreenId.ABOUT).pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), about))
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    deleted = [request.message_id for request in telegram.sent(DeleteMessage)]
    assert deleted == [MENU_MESSAGE_ID, FIRST_SENT_ID]


async def test_after_restart_pressing_old_menu_removes_it(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    ctx: BotContext,
    clients: ClientService,
    limiter: RateLimiter,
    content: Content,
) -> None:
    """Перезапуск стирает память: `/start` старое меню не видит, но нажатие на нём
    убирает и его, и меню, присланное уже после перезапуска."""
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))  # до перезапуска
    restarted = create_dispatcher(
        replace(ctx, delivery=ClientDelivery(bot, clients, TelegramPictures(content.pictures))),
        limiter,
    )

    await restarted.feed_update(bot, message(user(CLIENT_ID), "/start"))
    assert telegram.sent(DeleteMessage) == []  # старое меню боту неизвестно
    menu = MenuCallback(screen=ScreenId.MAIN).pack()
    await restarted.feed_update(bot, callback(user(CLIENT_ID), menu, message_id=FIRST_SENT_ID))

    deleted = [request.message_id for request in telegram.sent(DeleteMessage)]
    assert deleted == [FIRST_SENT_ID, FIRST_SENT_ID + 1]


async def test_start_strips_buttons_of_remembered_kept_text(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, ctx: BotContext
) -> None:
    """Сообщение с сохраняемым текстом (уведомление, оплата) новое меню не удаляет —
    у него гаснут только кнопки."""
    await ctx.delivery.send(CLIENT_ID, ctx.screens.about().kept())
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    assert telegram.sent(DeleteMessage) == []
    [stripped] = telegram.sent(EditMessageReplyMarkup)
    assert (stripped.chat_id, stripped.message_id) == (CLIENT_ID, FIRST_SENT_ID)
    assert stripped.reply_markup is None


async def test_client_who_blocked_bot_keeps_previous_menu(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """Новое меню не дошло — прошлое не трогаем, и бот помнит его дальше."""
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))
    telegram.fail_next(SendPhoto, TelegramForbiddenError, "Forbidden: bot was blocked by the user")
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))
    assert telegram.sent(DeleteMessage) == []

    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    [deleted] = telegram.sent(DeleteMessage)
    assert deleted.message_id == FIRST_SENT_ID


# Сбои Telegram при уборке прошлого меню: новое сообщение уже дошло, поэтому сбой
# уборки — не сбой отправки (иначе извинение, повтор уведомления, «ответ не отправлен»)
TAKE_DOWN_FAILURES: dict[str, tuple[type[TelegramAPIError], str, dict[str, Any]]] = {
    "retry_after": (TelegramRetryAfter, "Too Many Requests: retry after 5", {"retry_after": 5}),
    "network": (TelegramNetworkError, "HTTP Client says - ServerDisconnectedError", {}),
    "unexpected": (TelegramBadRequest, "Bad Request: MESSAGE_ID_INVALID", {}),
}


@pytest.mark.parametrize("failure", TAKE_DOWN_FAILURES.values(), ids=TAKE_DOWN_FAILURES)
async def test_failed_take_down_of_remembered_menu_does_not_fail_new_screen(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    alert_sender: FakeAlertSender,
    content: Content,
    failure: tuple[type[TelegramAPIError], str, dict[str, Any]],
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))
    error_type, text, extra = failure
    telegram.fail_next(DeleteMessage, error_type, text, **extra)

    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    assert telegram.sent(SendMessage) == []  # без извинения
    assert len(telegram.sent(SendPhoto)) == 2
    assert alert_sender.sent == []


@pytest.mark.parametrize("failure", TAKE_DOWN_FAILURES.values(), ids=TAKE_DOWN_FAILURES)
async def test_failed_take_down_of_pressed_menu_keeps_new_screen(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    alert_sender: FakeAlertSender,
    content: Content,
    failure: tuple[type[TelegramAPIError], str, dict[str, Any]],
) -> None:
    """Нажатое меню не убралось — новый экран остаётся: извинение его не уберёт."""
    error_type, text, extra = failure
    telegram.fail_next(DeleteMessage, error_type, text, **extra)

    await dispatcher.feed_update(
        bot, callback(user(CLIENT_ID), MenuCallback(screen=ScreenId.INSTRUCTIONS).pack())
    )

    [screen] = telegram.sent(SendMessage)
    assert screen.text == content.texts.get("instructions.choose")
    assert [d.message_id for d in telegram.sent(DeleteMessage)] == [MENU_MESSAGE_ID]
    assert alert_sender.sent == []


async def test_old_message_with_unknown_strip_error_is_harmless(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    """«В меню» под счётом в звёздах старше 48 часов: удалить нельзя, а снять кнопки
    Telegram отказывает неизвестной ошибкой — клиент всё равно получает меню."""
    telegram.fail_next(
        DeleteMessage, TelegramBadRequest, "Bad Request: message can't be deleted for everyone"
    )
    telegram.fail_next(
        EditMessageReplyMarkup, TelegramBadRequest, "Bad Request: REPLY_MARKUP_INVALID"
    )

    await dispatcher.feed_update(
        bot, callback(user(CLIENT_ID), MenuCallback(screen=ScreenId.MAIN).pack())
    )

    assert len(telegram.sent(SendPhoto)) == 1
    assert telegram.sent(SendMessage) == []  # без извинения
    assert alert_sender.sent == []


async def test_pressing_inaccessible_message_strips_its_buttons(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    """Telegram прислал нажатое сообщение недоступным (только номер, без кнопок и даты):
    его кнопки всё равно гаснут. Сохраняемый ли там текст, не видно — не удаляем."""
    press = Update(
        update_id=990,
        callback_query=CallbackQuery(
            id="cq-990",
            from_user=user(CLIENT_ID),
            chat_instance="ci",
            data=MenuCallback(screen=ScreenId.INSTRUCTIONS).pack(),
            message=InaccessibleMessage(chat=private_chat(CLIENT_ID), message_id=MENU_MESSAGE_ID),
        ),
    )

    await dispatcher.feed_update(bot, press)

    [screen] = telegram.sent(SendMessage)
    assert screen.text == content.texts.get("instructions.choose")
    assert telegram.sent(DeleteMessage) == []
    [stripped] = telegram.sent(EditMessageReplyMarkup)
    assert (stripped.chat_id, stripped.message_id) == (CLIENT_ID, MENU_MESSAGE_ID)
