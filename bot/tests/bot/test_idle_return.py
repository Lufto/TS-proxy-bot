"""Возвращение после суток простоя (задача 26): старая кнопка меню открывает главное меню."""

from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, SendMessage, SendPhoto
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot_updates import CLIENT_ID, MENU_MESSAGE_ID, callback, user

from tsproxy.bot.callbacks import (
    DeviceCallback,
    FaqCallback,
    FaqQuestion,
    InstructionsCallback,
    MenuCallback,
    PaymentCheckCallback,
    Platform,
    RevokeLinkCallback,
    ScreenId,
    StarsCallback,
    TariffCallback,
    TariffScreenCallback,
)
from tsproxy.bot.delivery import KEPT_TEXT_MARK
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.services.fakes import FakeClock
from tsproxy.services.profile import DEVICE_REF_LENGTH

MORE_THAN_A_DAY = timedelta(days=1, minutes=1)


def menu_data(screen: ScreenId) -> str:
    return MenuCallback(screen=screen).pack()


async def test_button_on_menu_older_than_a_day_opens_main_menu(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    clock: FakeClock,
) -> None:
    clock.advance(MORE_THAN_A_DAY)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(ScreenId.INSTRUCTIONS)))

    [sent] = telegram.sent(SendPhoto)
    assert sent.caption == content.texts.get("menu.title")
    [deleted] = telegram.sent(DeleteMessage)
    assert deleted.message_id == MENU_MESSAGE_ID  # старое убирается как обычно
    assert len(telegram.sent(AnswerCallbackQuery)) == 1


@pytest.mark.parametrize("idle", [timedelta(hours=23), timedelta(days=1)])
async def test_button_on_menu_younger_than_a_day_opens_pressed_screen(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    clock: FakeClock,
    idle: timedelta,
) -> None:
    clock.advance(idle)  # ровно сутки — ещё не «больше суток»

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(ScreenId.INSTRUCTIONS)))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("instructions.choose")


@pytest.mark.parametrize(
    "data",
    [
        menu_data(ScreenId.PROFILE),
        menu_data(ScreenId.DEVICES),
        menu_data(ScreenId.SUPPORT),
        menu_data(ScreenId.REVOKE_LINK),
        InstructionsCallback(platform=Platform.IOS).pack(),
        TariffScreenCallback(tariff_id="m1").pack(),
    ],
)
async def test_deep_screens_after_a_day_open_main_menu(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    clock: FakeClock,
    data: str,
) -> None:
    clock.advance(MORE_THAN_A_DAY)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    [sent] = telegram.sent(SendPhoto)
    assert sent.caption == content.texts.get("menu.title")


@pytest.mark.parametrize(
    "data",
    [
        menu_data(ScreenId.CONNECT),
        menu_data(ScreenId.TRIAL),
        TariffCallback(tariff_id="m1").pack(),
        StarsCallback(tariff_id="m1").pack(),
        PaymentCheckCallback(order_id=1).pack(),
        DeviceCallback(ref="0" * DEVICE_REF_LENGTH).pack(),
        RevokeLinkCallback().pack(),
        FaqCallback(question=FaqQuestion.CONNECT).pack(),
    ],
)
async def test_actions_after_a_day_are_not_replaced_by_main_menu(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    clock: FakeClock,
    data: str,
) -> None:
    """Подключение, пробный, оплата, удаление устройства, перевыпуск, частый вопрос
    в поддержке — намерение клиента."""
    clock.advance(MORE_THAN_A_DAY)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    assert telegram.sent(SendPhoto) == []  # главное меню — фото


async def test_notice_button_after_a_day_opens_its_screen(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    clock: FakeClock,
) -> None:
    """Кнопка уведомления («Продлить») — призыв из его текста, не переход по меню."""
    data = KEPT_TEXT_MARK + menu_data(ScreenId.TARIFFS)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Продлить", callback_data=data)]]
    )
    clock.advance(timedelta(days=5))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data, markup=markup))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("tariffs.title")
    assert telegram.sent(DeleteMessage) == []  # уведомление не удаляется


async def test_renew_button_of_notice_without_mark_opens_tariffs(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    clock: FakeClock,
) -> None:
    """Уведомление, отправленное до выкатки метки сохраняемого текста: «Продлить» на нём
    через неделю всё равно открывает тарифы — намерение клиента видно по кнопке."""
    data = menu_data(ScreenId.TARIFFS)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Продлить", callback_data=data)]]
    )
    clock.advance(timedelta(days=7))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data, markup=markup))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("tariffs.title")
