"""Поддержка на подделке Telegram: клиент пишет → тема в группе → ответ от «Поддержки»."""

import asyncio
import logging

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import CopyMessage, CreateForumTopic, SendMessage
from aiogram.types import Chat, Message, PhotoSize, Sticker, Update, User
from bot_updates import (
    ADMIN_CHAT_ID,
    ADMIN_ID,
    ALERTS_THREAD_ID,
    CLIENT_ID,
    DATE,
    OTHER_CLIENT_ID,
    private_chat,
    user,
)
from conftest import ManualMonotonic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.support_guard import KeyedLock
from tsproxy.bot.throttling import is_command
from tsproxy.content.loader import Content
from tsproxy.db.enums import MessageAuthor
from tsproxy.db.models import Client, SupportTicket, TicketMessage
from tsproxy.domain.support import SUPPORT_MESSAGES_PER_MINUTE
from tsproxy.logging_setup import quiet_library_loggers
from tsproxy.services.fakes import FakeAlertSender

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
QUESTION = "Не подключается на iPhone, что делать?"
SECONDS_PER_MESSAGE = 60 / SUPPORT_MESSAGES_PER_MINUTE
_ids = iter(range(1, 1_000_000))


def _update(message: Message) -> Update:
    return Update(update_id=next(_ids), message=message)


def client_text(text: str, *, sender: User | None = None, reply_to: int | None = None) -> Update:
    sender = sender or user(CLIENT_ID)
    chat = private_chat(sender.id)
    replied = None
    if reply_to is not None:
        replied = Message(message_id=reply_to, date=DATE, chat=chat, text="ответ поддержки")
    return _update(
        Message(
            message_id=next(_ids),
            date=DATE,
            chat=chat,
            from_user=sender,
            text=text,
            reply_to_message=replied,
        )
    )


def client_photo(caption: str | None = None) -> Update:
    sizes = [
        PhotoSize(file_id=f"photo-{size}", file_unique_id=f"u{size}", width=size, height=size)
        for size in (90, 1280)
    ]
    return _update(
        Message(
            message_id=next(_ids),
            date=DATE,
            chat=private_chat(CLIENT_ID),
            from_user=user(CLIENT_ID),
            photo=sizes,
            caption=caption,
        )
    )


def client_sticker() -> Update:
    sticker = Sticker(
        file_id="sticker",
        file_unique_id="s",
        type="regular",
        width=512,
        height=512,
        is_animated=False,
        is_video=False,
    )
    return _update(
        Message(
            message_id=next(_ids),
            date=DATE,
            chat=private_chat(CLIENT_ID),
            from_user=user(CLIENT_ID),
            sticker=sticker,
        )
    )


def topic_text(
    text: str,
    topic_id: int,
    *,
    sender_id: int = ADMIN_ID,
    reply_to: int | None = None,
    message_id: int | None = None,
) -> Update:
    """Сообщение в теме группы администраторов. Без явного reply Telegram ставит ответ
    на служебное сообщение «тема создана» (его номер равен номеру темы)."""
    replied = Message(
        message_id=reply_to or topic_id, date=DATE, chat=ADMIN_GROUP, text="сообщение в теме"
    )
    return _update(
        Message(
            message_id=message_id or next(_ids),
            date=DATE,
            chat=ADMIN_GROUP,
            from_user=user(sender_id, first_name="Админ", username=None),
            text=text,
            message_thread_id=topic_id,
            is_topic_message=True,
            reply_to_message=replied,
        )
    )


async def tickets(session_factory: async_sessionmaker[AsyncSession]) -> list[SupportTicket]:
    async with session_factory() as session:
        return list(await session.scalars(select(SupportTicket).order_by(SupportTicket.id)))


async def transcript(session_factory: async_sessionmaker[AsyncSession]) -> list[TicketMessage]:
    async with session_factory() as session:
        return list(await session.scalars(select(TicketMessage).order_by(TicketMessage.id)))


async def open_ticket(
    dispatcher: Dispatcher, bot: Bot, session_factory: async_sessionmaker[AsyncSession]
) -> int:
    """Клиент написал первый вопрос. Возвращает номер темы обращения."""
    await dispatcher.feed_update(bot, client_text(QUESTION))
    [ticket] = await tickets(session_factory)
    assert ticket.topic_id is not None
    return ticket.topic_id


def texts_to(telegram: FakeTelegramSession, chat_id: int) -> list[str]:
    return [m.text for m in telegram.sent(SendMessage) if m.chat_id == chat_id]


# --- Клиент → тема ---


async def test_first_message_opens_ticket_with_topic(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, client_text(QUESTION))

    [ticket] = await tickets(session_factory)
    [topic] = telegram.sent(CreateForumTopic)
    assert (topic.chat_id, topic.name) == (ADMIN_CHAT_ID, f"#{ticket.id} Иван")
    [header] = texts_to(telegram, ADMIN_CHAT_ID)
    assert f"Обращение #{ticket.id}" in header and str(CLIENT_ID) in header
    [copy] = telegram.sent(CopyMessage)
    assert (copy.chat_id, copy.message_thread_id, copy.from_chat_id) == (
        ADMIN_CHAT_ID,
        ticket.topic_id,
        CLIENT_ID,
    )
    assert texts_to(telegram, CLIENT_ID) == [
        content.texts.get("support.ticket_created", ticket_id=ticket.id)
    ]
    [saved] = await transcript(session_factory)
    assert (saved.ticket_id, saved.author, saved.text) == (
        ticket.id,
        MessageAuthor.CLIENT,
        QUESTION,
    )
    assert saved.client_message_id == copy.message_id
    assert saved.group_message_id is not None


async def test_next_messages_go_to_the_same_topic_without_confirmation(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)

    await dispatcher.feed_update(bot, client_text("И ещё вопрос"))

    assert len(telegram.sent(CreateForumTopic)) == 1
    assert [c.message_thread_id for c in telegram.sent(CopyMessage)] == [topic_id, topic_id]
    assert len(texts_to(telegram, CLIENT_ID)) == 1  # подтверждение — только на первое
    assert len(await tickets(session_factory)) == 1


async def test_photo_is_copied_and_only_file_id_is_stored(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await dispatcher.feed_update(bot, client_photo("Вот ошибка"))

    assert len(telegram.sent(CopyMessage)) == 1
    [saved] = await transcript(session_factory)
    assert (saved.text, saved.file_id, saved.file_kind) == ("Вот ошибка", "photo-1280", "photo")


async def test_sticker_is_not_forwarded(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, client_sticker())

    assert telegram.sent(CopyMessage) == []
    assert await tickets(session_factory) == []  # стикер не открывает обращение
    assert texts_to(telegram, CLIENT_ID) == [content.texts.get("support.unsupported")]


async def test_album_opens_one_ticket(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    album = [client_photo() for _ in range(5)]  # альбом — 5 сообщений одновременно

    await asyncio.gather(*(dispatcher.feed_update(bot, update) for update in album))

    assert len(telegram.sent(CreateForumTopic)) == 1
    assert len(await tickets(session_factory)) == 1
    copied = sorted(c.message_id for c in telegram.sent(CopyMessage))
    assert copied == sorted(u.message.message_id for u in album if u.message)
    assert len(texts_to(telegram, CLIENT_ID)) == 1


async def test_flood_asks_to_wait_once(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    monotonic: ManualMonotonic,
) -> None:
    for _ in range(SUPPORT_MESSAGES_PER_MINUTE + 3):
        await dispatcher.feed_update(bot, client_text("ау"))

    assert len(telegram.sent(CopyMessage)) == SUPPORT_MESSAGES_PER_MINUTE
    assert texts_to(telegram, CLIENT_ID).count(content.texts.get("support.flood")) == 1

    monotonic.now += SECONDS_PER_MESSAGE
    await dispatcher.feed_update(bot, client_text("ау"))
    assert len(telegram.sent(CopyMessage)) == SUPPORT_MESSAGES_PER_MINUTE + 1


async def test_clients_get_separate_topics(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await dispatcher.feed_update(bot, client_text(QUESTION))
    await dispatcher.feed_update(bot, client_text(QUESTION, sender=user(OTHER_CLIENT_ID)))

    first, second = await tickets(session_factory)
    assert first.topic_id != second.topic_id
    assert len(telegram.sent(CreateForumTopic)) == 2


async def test_client_name_with_markup_is_safe(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sender = user(CLIENT_ID, first_name='<a href="x">Иван</a>', username=None)
    await dispatcher.feed_update(bot, client_text(QUESTION, sender=sender))

    [ticket] = await tickets(session_factory)
    [topic] = telegram.sent(CreateForumTopic)
    assert topic.name == f'#{ticket.id} <a href="x">Иван</a>'  # тема — обычный текст
    [header] = texts_to(telegram, ADMIN_CHAT_ID)
    assert "&lt;a href" in header and "<a href" not in header


async def test_topic_creation_failure_raises_alert(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    telegram.fail_next(CreateForumTopic, TelegramBadRequest, "Bad Request: not enough rights")

    await dispatcher.feed_update(bot, client_text(QUESTION))

    [alert] = alert_sender.sent
    assert alert.thread_id == ALERTS_THREAD_ID
    assert "not enough rights" in alert.text
    assert texts_to(telegram, CLIENT_ID) == [content.texts.get("support.not_delivered")]

    # следующая попытка создаёт тему и подтверждает обращение
    await dispatcher.feed_update(bot, client_text(QUESTION))
    assert len(telegram.sent(CopyMessage)) == 1
    assert len(texts_to(telegram, CLIENT_ID)) == 2


async def test_deleted_topic_is_recreated(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    old_topic = await open_ticket(dispatcher, bot, session_factory)
    telegram.fail_next(CopyMessage, TelegramBadRequest, "Bad Request: message thread not found")

    await dispatcher.feed_update(bot, client_text("Вы тут?"))

    [ticket] = await tickets(session_factory)
    assert ticket.topic_id not in (None, old_topic)
    assert len(telegram.sent(CreateForumTopic)) == 2
    assert telegram.sent(CopyMessage)[-1].message_thread_id == ticket.topic_id


async def test_message_texts_are_not_logged(
    dispatcher: Dispatcher,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    quiet_library_loggers()  # как при запуске бота (`setup_logging`)
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    await dispatcher.feed_update(bot, topic_text("Секретный ответ поддержки", topic_id))

    assert QUESTION not in caplog.text
    assert "Секретный ответ" not in caplog.text


# --- Тема → клиент ---


async def test_admin_reply_goes_to_client_from_support(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)

    await dispatcher.feed_update(bot, topic_text("Переустановите Happ", topic_id))

    reply = texts_to(telegram, CLIENT_ID)[-1]
    assert reply == content.texts.get("support.reply_prefix") + "Переустановите Happ"
    saved = (await transcript(session_factory))[-1]
    assert (saved.author, saved.text) == (MessageAuthor.SUPPORT, "Переустановите Happ")
    assert saved.client_message_id is not None
    [ticket] = await tickets(session_factory)
    assert ticket.first_response_at is not None


async def test_note_is_not_sent_to_client(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    to_client_before = len(texts_to(telegram, CLIENT_ID))

    await dispatcher.feed_update(bot, topic_text("// клиент оплатил вчера", topic_id))

    assert len(texts_to(telegram, CLIENT_ID)) == to_client_before
    saved = (await transcript(session_factory))[-1]
    assert (saved.author, saved.client_message_id) == (MessageAuthor.NOTE, None)
    [ticket] = await tickets(session_factory)
    assert ticket.first_response_at is None  # заметка — не ответ клиенту


@pytest.mark.parametrize(
    ("sender_id", "text", "where"),
    [
        (CLIENT_ID, "Я тоже участник группы", "ticket"),  # не администратор
        (ADMIN_ID, "/stats", "ticket"),  # команда, а не ответ
        (ADMIN_ID, "Это в тревогах", "alerts"),  # тема не обращения
    ],
)
async def test_topic_messages_that_must_not_reach_client(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    sender_id: int,
    text: str,
    where: str,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    to_client_before = len(texts_to(telegram, CLIENT_ID))
    thread = topic_id if where == "ticket" else ALERTS_THREAD_ID

    await dispatcher.feed_update(bot, topic_text(text, thread, sender_id=sender_id))

    assert len(texts_to(telegram, CLIENT_ID)) == to_client_before
    assert len(await transcript(session_factory)) == 1  # только вопрос клиента


async def test_reply_to_a_message_is_threaded_on_both_sides(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    [question] = await transcript(session_factory)
    assert question.group_message_id is not None

    await dispatcher.feed_update(
        bot, topic_text("Про это", topic_id, reply_to=question.group_message_id)
    )
    answer = telegram.sent(SendMessage)[-1]
    assert answer.reply_parameters is not None
    assert answer.reply_parameters.message_id == question.client_message_id

    support_reply = (await transcript(session_factory))[-1]
    assert support_reply.client_message_id is not None
    await dispatcher.feed_update(
        bot, client_text("Не помогло", reply_to=support_reply.client_message_id)
    )
    copy = telegram.sent(CopyMessage)[-1]
    assert copy.reply_parameters is not None
    assert copy.reply_parameters.message_id == support_reply.group_message_id


async def test_reply_to_client_who_blocked_bot(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    telegram.fail_next(
        SendMessage, TelegramForbiddenError, "Forbidden: bot was blocked by the user"
    )

    await dispatcher.feed_update(bot, topic_text("Вы тут?", topic_id))

    notice = telegram.sent(SendMessage)[-1]
    assert (notice.chat_id, notice.message_thread_id, notice.text) == (
        ADMIN_CHAT_ID,
        topic_id,
        content.admin_texts.get("support.client_blocked"),
    )
    async with session_factory() as session:
        client = await session.scalar(select(Client).where(Client.telegram_id == CLIENT_ID))
    assert client is not None and client.bot_blocked_at is not None
    saved = (await transcript(session_factory))[-1]
    assert (saved.author, saved.client_message_id) == (MessageAuthor.SUPPORT, None)


async def test_rejected_reply_is_reported_in_topic(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    telegram.fail_next(SendMessage, TelegramBadRequest, "Bad Request: message is too long")

    await dispatcher.feed_update(bot, topic_text("Очень длинный ответ", topic_id))

    notice = telegram.sent(SendMessage)[-1]
    assert notice.message_thread_id == topic_id
    assert "message is too long" in notice.text
    assert len(await transcript(session_factory)) == 1  # не доставлено — не в переписке


async def test_admin_photo_goes_to_client_with_signature(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    photo = Message(
        message_id=next(_ids),
        date=DATE,
        chat=ADMIN_GROUP,
        from_user=user(ADMIN_ID, first_name="Админ", username=None),
        photo=[PhotoSize(file_id="admin-photo", file_unique_id="a", width=800, height=600)],
        caption="Нажмите сюда",
        message_thread_id=topic_id,
        is_topic_message=True,
    )

    await dispatcher.feed_update(bot, _update(photo))

    copy = telegram.sent(CopyMessage)[-1]
    assert (copy.chat_id, copy.from_chat_id, copy.message_id) == (
        CLIENT_ID,
        ADMIN_CHAT_ID,
        photo.message_id,
    )
    assert copy.caption == content.texts.get("support.reply_prefix") + "Нажмите сюда"
    saved = (await transcript(session_factory))[-1]
    assert (saved.author, saved.file_id, saved.file_kind) == (
        MessageAuthor.SUPPORT,
        "admin-photo",
        "photo",
    )


# --- Мелкие правила ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start", True),
        ("/support@ts_proxy_bot", True),
        ("/stats 7", True),
        ("// заметка", False),
        ("/путь к файлу", False),
        ("Здравствуйте", False),
    ],
)
def test_command_detection(text: str, expected: bool) -> None:
    message = Message(message_id=1, date=DATE, chat=private_chat(CLIENT_ID), text=text)
    assert is_command(message) is expected


async def test_keyed_lock_forgets_free_keys() -> None:
    lock = KeyedLock()
    async with lock(CLIENT_ID):
        assert len(lock) == 1
    assert len(lock) == 0
