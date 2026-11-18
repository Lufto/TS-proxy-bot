"""`/start`, живое меню, команды, инструкции, заглушки."""

import re

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
)
from aiogram.types import InlineKeyboardMarkup
from bot_updates import CLIENT_ID, MENU_MESSAGE_ID, NOW, callback, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import InstructionsCallback, MenuCallback, Platform, ScreenId
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.models import Client
from tsproxy.services.fakes import FakeClock

# Запасные приложения (config/site.yaml): в боте их нет, они на сайте
FALLBACK_APPS = re.compile(r"v2RayTun|OneXray|Hiddify|v2rayN|Throne", re.IGNORECASE)


async def stored_client(
    session_factory: async_sessionmaker[AsyncSession], telegram_id: int = CLIENT_ID
) -> Client | None:
    async with session_factory() as session:
        return await session.scalar(select(Client).where(Client.telegram_id == telegram_id))


def button_texts(markup: InlineKeyboardMarkup | None) -> list[str]:
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]


def menu_data(screen: ScreenId) -> str:
    return MenuCallback(screen=screen).pack()


async def test_start_creates_client_and_sends_menu(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID, username="ivan"), "/start"))

    client = await stored_client(session_factory)
    assert client is not None
    assert (client.name, client.username, client.first_seen_at) == ("Иван", "ivan", NOW)
    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == CLIENT_ID
    assert sent.text == content.texts.get("menu.title")
    assert content.texts.get("buttons.trial") in button_texts(sent.reply_markup)  # type: ignore[arg-type]


async def test_start_from_site_support_link_opens_support(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    """Кнопка «Написать в поддержку» на сайте: `t.me/<бот>?start=support`."""
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start support"))

    assert await stored_client(session_factory) is not None
    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("support.intro")


async def test_start_with_unknown_parameter_sends_menu(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start promo"))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("menu.title")


async def test_start_updates_name_and_clears_blocked_mark(
    dispatcher: Dispatcher,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    async with session_factory() as session:
        session.add(
            Client(telegram_id=CLIENT_ID, name="Старое", first_seen_at=NOW, bot_blocked_at=NOW)
        )
        await session.commit()

    await dispatcher.feed_update(bot, message(user(CLIENT_ID, username="new_name"), "/start"))

    client = await stored_client(session_factory)
    assert client is not None
    assert (client.name, client.username, client.bot_blocked_at) == ("Иван", "new_name", None)
    assert client.first_seen_at == NOW  # дата первого визита не меняется


async def test_menu_hides_trial_button_after_trial(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=CLIENT_ID, first_seen_at=NOW, trial_granted_at=NOW))
        await session.commit()

    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    [sent] = telegram.sent(SendMessage)
    buttons = button_texts(sent.reply_markup)  # type: ignore[arg-type]
    assert content.texts.get("buttons.trial") not in buttons
    assert content.texts.get("buttons.connect") in buttons


async def test_main_menu_has_no_documents(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    [sent] = telegram.sent(SendMessage)
    buttons = button_texts(sent.reply_markup)  # type: ignore[arg-type]
    assert "Соглашение" not in buttons and "Конфиденциальность" not in buttons


async def test_about_has_site_documents_and_back(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(ScreenId.ABOUT)))

    [sent] = telegram.sent(SendMessage)
    markup = sent.reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    buttons = [(b.text, b.url, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert buttons == [
        ("Сайт", "https://ts-proxy.com", None),
        ("Соглашение", "https://ts-proxy.com/terms", None),
        ("Конфиденциальность", "https://ts-proxy.com/privacy", None),
        ("В меню", None, menu_data(ScreenId.MAIN)),
    ]


@pytest.mark.parametrize(
    ("screen", "text_key"),
    [
        (ScreenId.ABOUT, "about.text"),
        (ScreenId.INSTRUCTIONS, "instructions.choose"),
        (ScreenId.TARIFFS, "tariffs.title"),
        (ScreenId.SUPPORT, "support.intro"),
        (ScreenId.MAIN, "menu.title"),
    ],
)
async def test_menu_button_sends_new_screen_and_deletes_pressed_one(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    screen: ScreenId,
    text_key: str,
) -> None:
    """Живое меню: экран приходит новым сообщением (чат поднимается в списке),
    нажатое сообщение удаляется — в чате одно меню."""
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(screen)))

    [sent] = telegram.sent(SendMessage)
    assert (sent.chat_id, sent.text) == (CLIENT_ID, content.texts.get(text_key))
    [deleted] = telegram.sent(DeleteMessage)
    assert (deleted.chat_id, deleted.message_id) == (CLIENT_ID, MENU_MESSAGE_ID)
    assert telegram.requests.index(sent) < telegram.requests.index(deleted)  # сначала новое
    assert telegram.sent(EditMessageText) == []
    assert len(telegram.sent(AnswerCallbackQuery)) == 1  # «часики» на кнопке погашены


@pytest.mark.parametrize(
    "reason",
    [
        "Bad Request: message can't be deleted for everyone",  # старше 48 часов
        "Bad Request: message to delete not found",  # клиент уже удалил
        "Bad Request: message can't be deleted",  # вообще нельзя удалить
    ],
)
async def test_pressed_message_that_cannot_be_deleted_loses_buttons(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    reason: str,
) -> None:
    telegram.fail_next(DeleteMessage, TelegramBadRequest, reason)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(ScreenId.ABOUT)))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("about.text")  # без извинения об ошибке
    [stripped] = telegram.sent(EditMessageReplyMarkup)
    assert (stripped.chat_id, stripped.message_id) == (CLIENT_ID, MENU_MESSAGE_ID)
    assert stripped.reply_markup is None


@pytest.mark.parametrize(
    "reason",
    [
        "Bad Request: message is not modified: specified new message content and reply "
        "markup are exactly the same as a current content and reply markup of the message",
        "Bad Request: message to edit not found",
    ],
)
async def test_buttons_already_gone_is_not_an_error(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    reason: str,
) -> None:
    telegram.fail_next(
        DeleteMessage, TelegramBadRequest, "Bad Request: message to delete not found"
    )
    telegram.fail_next(EditMessageReplyMarkup, TelegramBadRequest, reason)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(ScreenId.ABOUT)))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("about.text")  # без извинения об ошибке


async def test_client_who_blocked_bot_keeps_pressed_menu(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """Новый экран не дошёл — старое меню не трогаем: иначе у клиента не осталось бы ни одного."""
    telegram.fail_next(
        SendMessage, TelegramForbiddenError, "Forbidden: bot was blocked by the user"
    )

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(ScreenId.ABOUT)))

    assert telegram.sent(DeleteMessage) == []
    assert telegram.sent(EditMessageReplyMarkup) == []


@pytest.mark.parametrize("platform", list(Platform))
async def test_instructions_for_each_platform(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    platform: Platform,
) -> None:
    data = InstructionsCallback(platform=platform).pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get(f"instructions.{platform.value}")
    # задача 16: только Happ, запасные приложения — на сайте, ссылка ведёт в раздел устройства
    assert "Happ" in sent.text
    assert FALLBACK_APPS.findall(sent.text) == []
    assert f'<a href="https://ts-proxy.com/help/{platform.value}/">' in sent.text


@pytest.mark.parametrize(
    ("command", "text_key"),
    [("/buy", "tariffs.title"), ("/support", "support.intro")],
)
async def test_commands_open_the_same_screens(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    command: str,
    text_key: str,
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), command))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get(text_key)


async def test_unknown_button_data_is_rejected(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), "menu:give_me_admin"))

    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.texts.get("errors.stale_button")
    assert telegram.sent(SendMessage) == []
    assert telegram.sent(DeleteMessage) == []


async def test_unknown_command_gets_a_hint(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/whatever"))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("fallback.unknown_command")


async def test_help_lists_client_commands(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/help"))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("help.text")


async def test_connect_is_the_green_main_button(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    """Главная кнопка экрана — зелёная, остальные — обычного цвета (спека «Кнопки»)."""
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    [sent] = telegram.sent(SendMessage)
    assert isinstance(sent.reply_markup, InlineKeyboardMarkup)
    styles = {b.text: b.style for row in sent.reply_markup.inline_keyboard for b in row}
    assert styles.pop(content.texts.get("buttons.connect")) == "success"
    assert set(styles.values()) == {None}
