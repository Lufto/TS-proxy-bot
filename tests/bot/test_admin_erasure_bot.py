"""«🗑 Удалить данные клиента» на подделке Telegram (задача 24): кнопка в карточке, предпросмотр
с подтверждением, удаление тем обращений, отказ не-администратору, сбой удаления темы."""

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteForumTopic, EditMessageText, SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Message, Update
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, CLIENT_ID, DATE, callback, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_callbacks import AdminEraseCallback
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.enums import MessageAuthor
from tsproxy.db.models import Client, Event, SupportTicket, TicketMessage
from tsproxy.services.clients import ClientProfile, ClientService
from tsproxy.services.erasure import ERASE_ACTION
from tsproxy.services.events import EventKind
from tsproxy.services.support import MessageContent

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
STRANGER_ID = 333
TOPIC_ID = 55
CALLBACK_DATA_MAX_BYTES = 64
MAX_TELEGRAM_ID = 2**63 - 1


def press(data: str, *, sender_id: int = ADMIN_ID, topic_id: int | None = None) -> Update:
    update = callback(user(sender_id, first_name="Админ"), data, chat_id=ADMIN_CHAT_ID)
    if topic_id is None:
        return update
    assert update.callback_query is not None
    in_topic = Message(
        message_id=5,
        date=DATE,
        chat=ADMIN_GROUP,
        text="карточка",
        message_thread_id=topic_id,
        is_topic_message=True,
    )
    query = update.callback_query.model_copy(update={"message": in_topic})
    return update.model_copy(update={"callback_query": query})


def ask_data() -> str:
    return AdminEraseCallback(telegram_id=CLIENT_ID).pack()


def confirm_data() -> str:
    return AdminEraseCallback(telegram_id=CLIENT_ID, confirm=True).pack()


def group_messages(telegram: FakeTelegramSession) -> list[SendMessage]:
    return [m for m in telegram.sent(SendMessage) if m.chat_id == ADMIN_CHAT_ID]


async def stored_client(session_factory: async_sessionmaker[AsyncSession]) -> Client:
    async with session_factory() as session:
        found = await session.scalar(select(Client).where(Client.telegram_id == CLIENT_ID))
    assert found is not None
    return found


async def stored_messages(session_factory: async_sessionmaker[AsyncSession]) -> list[TicketMessage]:
    async with session_factory() as session:
        return list(await session.scalars(select(TicketMessage)))


@pytest.fixture
async def client_with_ticket(clients: ClientService, ctx: BotContext) -> None:
    """Клиент «Иван» (@ivan) с обращением в теме `TOPIC_ID` и одним сообщением."""
    client = await clients.register(ClientProfile(CLIENT_ID, "Иван", "ivan"))
    ticket = await ctx.support.current_ticket(client.id)
    await ctx.support.attach_topic(ticket.id, TOPIC_ID)
    await ctx.support.record_message(
        ticket.id,
        MessageAuthor.CLIENT,
        MessageContent("Удалите мои данные"),
        client_message_id=1,
        group_message_id=2,
    )


async def test_card_has_erase_button(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, client_with_ticket: None
) -> None:
    command = message(user(ADMIN_ID, first_name="Админ"), f"/client {CLIENT_ID}", chat=ADMIN_GROUP)
    await dispatcher.feed_update(bot, command)

    [card] = group_messages(telegram)
    assert isinstance(card.reply_markup, InlineKeyboardMarkup)
    data = [b.callback_data for row in card.reply_markup.inline_keyboard for b in row]
    assert ask_data() in data


async def test_erase_button_shows_preview_and_deletes_nothing(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    client_with_ticket: None,
) -> None:
    await dispatcher.feed_update(bot, press(ask_data()))

    [ask] = group_messages(telegram)
    assert "Иван" in ask.text and "ivan" in ask.text
    assert isinstance(ask.reply_markup, InlineKeyboardMarkup)
    data = [b.callback_data for row in ask.reply_markup.inline_keyboard for b in row]
    assert confirm_data() in data
    assert telegram.sent(DeleteForumTopic) == []
    assert (await stored_client(session_factory)).name == "Иван"


async def test_confirm_deletes_topic_and_data(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
    client_with_ticket: None,
) -> None:
    await dispatcher.feed_update(bot, press(confirm_data()))

    [removed] = telegram.sent(DeleteForumTopic)
    assert (removed.chat_id, removed.message_thread_id) == (ADMIN_CHAT_ID, TOPIC_ID)
    client = await stored_client(session_factory)
    assert (client.name, client.username) == (None, None)
    assert await stored_messages(session_factory) == []
    [done] = telegram.sent(EditMessageText)  # кнопку подтверждения больше не нажать
    assert done.text == content.admin_texts.get(
        "admin.erase.done", telegram_id=CLIENT_ID, tickets=1, messages=1, topics=1
    )
    assert done.reply_markup is None
    async with session_factory() as session:
        [event] = await session.scalars(
            select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value)
        )
    assert (event.admin_id, (event.details or {}).get("action")) == (ADMIN_ID, ERASE_ACTION)


async def test_confirm_inside_the_deleted_topic_reports_to_general_chat(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, client_with_ticket: None
) -> None:
    await dispatcher.feed_update(bot, press(confirm_data(), topic_id=TOPIC_ID))

    assert telegram.sent(EditMessageText) == []
    [done] = group_messages(telegram)
    assert done.message_thread_id is None
    assert str(CLIENT_ID) in done.text


async def test_already_deleted_topic_does_not_stop_erasure(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    client_with_ticket: None,
) -> None:
    telegram.fail_next(DeleteForumTopic, TelegramBadRequest, "Bad Request: TOPIC_ID_INVALID")

    await dispatcher.feed_update(bot, press(confirm_data()))

    assert await stored_messages(session_factory) == []
    async with session_factory() as session:
        assert list(await session.scalars(select(SupportTicket))) == []


async def test_topic_removal_failure_keeps_data_and_confirm_button(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    client_with_ticket: None,
) -> None:
    telegram.fail_next(
        DeleteForumTopic, TelegramBadRequest, "Bad Request: not enough rights to manage topics"
    )

    await dispatcher.feed_update(bot, press(confirm_data()))

    assert telegram.sent(EditMessageText) == []  # сообщение с «✅ Да» осталось — повтор безопасен
    [failed] = group_messages(telegram)
    assert "not enough rights" in failed.text
    assert (await stored_client(session_factory)).name == "Иван"
    assert len(await stored_messages(session_factory)) == 1


async def test_stranger_cannot_erase(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    client_with_ticket: None,
) -> None:
    await dispatcher.feed_update(bot, press(confirm_data(), sender_id=STRANGER_ID))
    await dispatcher.feed_update(bot, press(ask_data(), sender_id=STRANGER_ID))

    assert telegram.sent(DeleteForumTopic) == []
    assert group_messages(telegram) == []
    assert (await stored_client(session_factory)).name == "Иван"


async def test_erase_of_unknown_client(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, press(ask_data()))

    [answer] = group_messages(telegram)
    assert answer.text == content.admin_texts.get("admin.client_not_found", telegram_id=CLIENT_ID)


def test_erase_callback_data_fits_telegram_limit() -> None:
    packed = AdminEraseCallback(telegram_id=MAX_TELEGRAM_ID, confirm=True).pack()
    assert len(packed.encode()) <= CALLBACK_DATA_MAX_BYTES
