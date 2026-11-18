"""`/start`, меню в одном сообщении, команды, инструкции, заглушки."""

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import InlineKeyboardMarkup
from bot_updates import CLIENT_ID, MENU_MESSAGE_ID, NOW, callback, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import InstructionsCallback, MenuCallback, Platform, ScreenId
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.models import Client
from tsproxy.services.fakes import FakeClock


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
async def test_menu_button_edits_the_same_message(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    screen: ScreenId,
    text_key: str,
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(screen)))

    [edited] = telegram.sent(EditMessageText)
    assert (edited.chat_id, edited.message_id) == (CLIENT_ID, MENU_MESSAGE_ID)
    assert edited.text == content.texts.get(text_key)
    assert telegram.sent(SendMessage) == []  # нового сообщения нет — меню одно
    assert len(telegram.sent(AnswerCallbackQuery)) == 1  # «часики» на кнопке погашены


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

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get(f"instructions.{platform.value}")
    assert "Happ" in edited.text and "v2RayTun" in edited.text


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


async def test_same_button_twice_is_not_an_error(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    telegram.fail_next(
        EditMessageText, TelegramBadRequest, "Bad Request: message is not modified: …"
    )

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(ScreenId.ABOUT)))

    assert telegram.sent(SendMessage) == []  # ни извинения, ни нового меню


async def test_old_message_is_replaced_by_new_one(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    telegram.fail_next(EditMessageText, TelegramBadRequest, "Bad Request: message can't be edited")

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), menu_data(ScreenId.ABOUT)))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("about.text")


async def test_unknown_button_data_is_rejected(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), "menu:give_me_admin"))

    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.texts.get("errors.stale_button")
    assert telegram.sent(EditMessageText) == []


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
