"""Живое меню: `/start` и экраны не по нажатию убирают прошлое меню (задача 24).

Бот помнит последнее живое меню клиента в памяти; после перезапуска память пуста,
и старое меню убирается при нажатии на нём.
"""

from dataclasses import replace

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import DeleteMessage, EditMessageReplyMarkup, SendMessage
from bot_updates import CLIENT_ID, MENU_MESSAGE_ID, callback, message, user

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.dispatcher import create_dispatcher
from tsproxy.bot.fakes import FIRST_MESSAGE_ID, FakeTelegramSession
from tsproxy.bot.throttling import RateLimiter
from tsproxy.services.clients import ClientService

# Подделка Telegram нумерует отправленные сообщения подряд
FIRST_SENT_ID = FIRST_MESSAGE_ID + 1


async def test_second_start_removes_previous_menu(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    _, second = telegram.sent(SendMessage)
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
) -> None:
    """Перезапуск стирает память: `/start` старое меню не видит, но нажатие на нём
    убирает и его, и меню, присланное уже после перезапуска."""
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))  # до перезапуска
    restarted = create_dispatcher(replace(ctx, delivery=ClientDelivery(bot, clients)), limiter)

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
    telegram.fail_next(
        SendMessage, TelegramForbiddenError, "Forbidden: bot was blocked by the user"
    )
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))
    assert telegram.sent(DeleteMessage) == []

    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    [deleted] = telegram.sent(DeleteMessage)
    assert deleted.message_id == FIRST_SENT_ID
