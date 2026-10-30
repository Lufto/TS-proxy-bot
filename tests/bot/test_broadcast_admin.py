"""Рассылка и статистика в группе администраторов (задачи 19 и 32).

Главное (критерий задачи 19): рассылка не уходит без подтверждения и не трогает нынешних
клиентов панели; повторное «✅ Отправить» не шлёт второй раз.
Задача 32: фото с подписью уходит каждому клиенту копией предпросмотра; альбом — отказ.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from itertools import count

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, CopyMessage, EditMessageReplyMarkup, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Dice,
    ForumTopicCreated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Location,
    Message,
    MessageEntity,
    PhotoSize,
    Update,
    User,
    Voice,
)
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, BOT_ID, DATE, message, user
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_broadcast import (
    AdminBroadcastCallback,
    BroadcastAction,
    TelegramBroadcastSender,
    command_body_html,
    copy_refusal,
    create_admin_broadcast_router,
)
from tsproxy.bot.admin_stats import create_admin_stats_router, format_duration
from tsproxy.bot.context import BotContext
from tsproxy.bot.dispatcher import create_dispatcher
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.throttling import RateLimiter
from tsproxy.content.loader import Content
from tsproxy.db.models import Client
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.services.broadcast import (
    BroadcastCopy,
    BroadcastService,
    BroadcastText,
    BroadcastThrottled,
    DeliveryOutcome,
)
from tsproxy.services.clients import ClientService
from tsproxy.services.fakes import FakeClock
from tsproxy.services.stats import StatsService

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
TOPIC_ID = 55  # тема группы, где администратор пишет /broadcast
PREVIEW_MESSAGE_ID = 3001
STRANGER_ID = 333
BOT_CLIENTS = (111, 222)
BLOCKED_CLIENT = 444
SEND = AdminBroadcastCallback(action=BroadcastAction.SEND).pack()
CANCEL = AdminBroadcastCallback(action=BroadcastAction.CANCEL).pack()
STOP = AdminBroadcastCallback(action=BroadcastAction.STOP).pack()
# Текст предпросмотра, как его вернёт Telegram: обычный текст и разметка отдельно
PREVIEW_TEXT = "Скидка 20% & подарок <тут>"
PREVIEW_ENTITIES = [MessageEntity(type="bold", offset=0, length=6)]
PREVIEW_HTML = "<b>Скидка</b> 20% &amp; подарок &lt;тут&gt;"
_ids = count(10_000)


@dataclass
class NoSleep:
    waits: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture
def broadcasts(
    session_factory: async_sessionmaker[AsyncSession], ctx: BotContext, bot: Bot, clock: FakeClock
) -> BroadcastService:
    sender = TelegramBroadcastSender(bot, ctx.delivery)
    return BroadcastService(session_factory, ctx.events, clock, sender, sleep=NoSleep())


@pytest.fixture
def admin_dispatcher(
    ctx: BotContext,
    limiter: RateLimiter,
    broadcasts: BroadcastService,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> Dispatcher:
    return create_dispatcher(
        ctx,
        limiter,
        extra_routers=[
            create_admin_stats_router(ctx.access, StatsService(session_factory, remnawave, clock)),
            create_admin_broadcast_router(ctx.access, broadcasts),
        ],
    )


@pytest.fixture
async def audience(
    session_factory: async_sessionmaker[AsyncSession], remnawave: FakeRemnawave
) -> None:
    """Два клиента бота, один заблокировавший бота и 28 нынешних клиентов панели."""
    async with session_factory() as session:
        session.add_all([Client(telegram_id=telegram_id) for telegram_id in BOT_CLIENTS])
        session.add(Client(telegram_id=BLOCKED_CLIENT, bot_blocked_at=DATE))
        await session.commit()
    for number in range(28):
        remnawave.add_foreign_account(f"manual_{number}", telegram_id=5000 + number)


def broadcast_command(text: str, *, sender_id: int = ADMIN_ID) -> Update:
    update = message(user(sender_id, first_name="Админ"), text, chat=ADMIN_GROUP)
    assert update.message is not None
    topic_message = update.message.model_copy(
        update={"message_thread_id": TOPIC_ID, "is_topic_message": True}
    )
    return update.model_copy(update={"message": topic_message})


def preview_press(
    data: str,
    *,
    sender_id: int = ADMIN_ID,
    chat: Chat = ADMIN_GROUP,
    message_id: int = PREVIEW_MESSAGE_ID,
) -> Update:
    """Нажатие под сообщением предпросмотра: Telegram присылает его текст и разметку."""
    return Update(
        update_id=next(_ids),
        callback_query=CallbackQuery(
            id=str(next(_ids)),
            from_user=user(sender_id, first_name="Админ"),
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=message_id,
                date=DATE,
                chat=chat,
                text=PREVIEW_TEXT,
                entities=PREVIEW_ENTITIES,
                message_thread_id=TOPIC_ID,
                is_topic_message=True,
            ),
        ),
    )


def to_clients(telegram: FakeTelegramSession) -> list[SendMessage]:
    return [m for m in telegram.sent(SendMessage) if m.chat_id != ADMIN_CHAT_ID]


def to_admins(telegram: FakeTelegramSession) -> list[SendMessage]:
    return [m for m in telegram.sent(SendMessage) if m.chat_id == ADMIN_CHAT_ID]


# --- Предпросмотр ---


@pytest.mark.usefixtures("audience")
async def test_command_shows_preview_and_sends_nothing_to_clients(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await admin_dispatcher.feed_update(bot, broadcast_command("/broadcast Привет & <всем>"))

    header, preview = telegram.sent(SendMessage)
    assert header.text == content.admin_texts.get(
        "admin.broadcast.preview_header", recipients=2, blocked=1
    )
    assert preview.text == "Привет &amp; &lt;всем&gt;"
    assert preview.parse_mode == ParseMode.HTML
    assert preview.message_thread_id == TOPIC_ID
    assert preview.reply_markup is not None
    assert to_clients(telegram) == []  # без «✅ Отправить» клиентам ничего не уходит


async def test_command_without_text_shows_usage(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await admin_dispatcher.feed_update(bot, broadcast_command("/broadcast   "))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.admin_texts.get("admin.broadcast.usage")


async def test_command_from_stranger_is_ignored(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await admin_dispatcher.feed_update(
        bot, broadcast_command("/broadcast Привет", sender_id=STRANGER_ID)
    )

    assert not any(m.text.startswith("Привет") for m in telegram.sent(SendMessage))


# --- Отправка ---


@pytest.mark.usefixtures("audience")
async def test_send_goes_to_bot_clients_only_with_preview_markup(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await admin_dispatcher.feed_update(bot, preview_press(SEND))
    await broadcasts.wait()

    sent = to_clients(telegram)
    assert [m.chat_id for m in sent] == list(BOT_CLIENTS)  # без заблокировавшего и без панели
    assert {m.text for m in sent} == {PREVIEW_HTML}
    assert {m.parse_mode for m in sent} == {ParseMode.HTML}
    assert remnawave.writes == []
    [edit] = telegram.sent(EditMessageReplyMarkup)
    assert edit.message_id == PREVIEW_MESSAGE_ID and edit.reply_markup is None
    started, finished = to_admins(telegram)
    assert {started.message_thread_id, finished.message_thread_id} == {TOPIC_ID}
    assert started.text.startswith("📣")
    assert (
        content.admin_texts.get("admin.broadcast.counts", delivered=2, total=2, blocked=0, failed=0)
        in finished.text
    )


@pytest.mark.usefixtures("audience")
async def test_double_press_sends_once(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
    content: Content,
) -> None:
    await admin_dispatcher.feed_update(bot, preview_press(SEND))
    await broadcasts.wait()
    await admin_dispatcher.feed_update(bot, preview_press(SEND))
    await broadcasts.wait()

    assert len(to_clients(telegram)) == len(BOT_CLIENTS)
    answers = telegram.sent(AnswerCallbackQuery)
    assert answers[-1].text == content.admin_texts.get("admin.broadcast.already_handled")


@pytest.mark.usefixtures("audience")
async def test_cancel_then_send_sends_nothing(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
) -> None:
    await admin_dispatcher.feed_update(bot, preview_press(CANCEL))
    await admin_dispatcher.feed_update(bot, preview_press(SEND))
    await broadcasts.wait()

    assert to_clients(telegram) == []


@pytest.mark.usefixtures("audience")
async def test_stranger_press_is_refused(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
    content: Content,
) -> None:
    await admin_dispatcher.feed_update(bot, preview_press(SEND, sender_id=STRANGER_ID))
    await broadcasts.wait()

    assert to_clients(telegram) == []
    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.admin_texts.get("admin.not_admin")


@pytest.mark.usefixtures("audience")
async def test_admin_press_outside_admin_group_is_refused(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
) -> None:
    other_group = Chat(id=-999, type="supergroup", title="Чужая")
    await admin_dispatcher.feed_update(bot, preview_press(SEND, chat=other_group))
    await broadcasts.wait()

    assert to_clients(telegram) == []


@pytest.mark.usefixtures("audience")
async def test_stop_without_running_broadcast(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await admin_dispatcher.feed_update(bot, preview_press(STOP))

    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.admin_texts.get("admin.broadcast.not_running")


# --- Отправитель ---


async def test_sender_marks_blocked_client(
    bot: Bot,
    ctx: BotContext,
    telegram: FakeTelegramSession,
    clients: ClientService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=111))
        await session.commit()
    telegram.fail_next(SendMessage, TelegramForbiddenError, "bot was blocked by the user")

    outcome = await TelegramBroadcastSender(bot, ctx.delivery).send(
        111, BroadcastText(PREVIEW_HTML)
    )

    assert outcome is DeliveryOutcome.BLOCKED
    client = await clients.find(111)
    assert client is not None and client.bot_blocked_at is not None


async def test_sender_turns_flood_wait_into_throttled(
    bot: Bot, ctx: BotContext, telegram: FakeTelegramSession
) -> None:
    telegram.fail_next(SendMessage, TelegramRetryAfter, "Too Many Requests", retry_after=7)

    with pytest.raises(BroadcastThrottled) as raised:
        await TelegramBroadcastSender(bot, ctx.delivery).send(111, BroadcastText(PREVIEW_HTML))

    assert raised.value.retry_after == 7


# --- Копия сообщения: фото, видео, файл… (задача 32) ---

SOURCE_MESSAGE_ID = 2001  # сообщение администратора, на которое он ответил /broadcast
CONFIRM_MESSAGE_ID = 3002  # «получат N…» с кнопками под копией-предпросмотром
PHOTO = [PhotoSize(file_id="photo", file_unique_id="photo-u", width=640, height=480)]
CAPTION = "Новогодняя скидка"
CAPTION_ENTITIES = [MessageEntity(type="bold", offset=0, length=10)]
SEND_COPY = AdminBroadcastCallback(action=BroadcastAction.SEND_COPY).pack()
BOT_USER = User(id=BOT_ID, is_bot=True, first_name="TS Proxy")


def photo_message(
    *, message_id: int = SOURCE_MESSAGE_ID, from_user: User | None = None, **extra: object
) -> Message:
    return Message(
        message_id=message_id,
        date=DATE,
        chat=ADMIN_GROUP,
        from_user=from_user or user(ADMIN_ID, first_name="Админ"),
        photo=PHOTO,
        caption=CAPTION,
        caption_entities=CAPTION_ENTITIES,
        message_thread_id=TOPIC_ID,
        is_topic_message=True,
        **extra,
    )


def broadcast_reply(source: Message, text: str = "/broadcast") -> Update:
    """Администратор отвечает командой на сообщение в теме."""
    update = broadcast_command(text)
    assert update.message is not None
    reply = update.message.model_copy(update={"reply_to_message": source})
    return update.model_copy(update={"message": reply})


def confirm_press(data: str, *, preview: Message | None) -> Update:
    """Нажатие под «получат N…»: Telegram присылает и сообщение, на которое оно отвечает."""
    return Update(
        update_id=next(_ids),
        callback_query=CallbackQuery(
            id=str(next(_ids)),
            from_user=user(ADMIN_ID, first_name="Админ"),
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=CONFIRM_MESSAGE_ID,
                date=DATE,
                chat=ADMIN_GROUP,
                text="📣 Предпросмотр рассылки",
                message_thread_id=TOPIC_ID,
                is_topic_message=True,
                reply_to_message=preview,
            ),
        ),
    )


def copies_to_clients(telegram: FakeTelegramSession) -> list[CopyMessage]:
    return [c for c in telegram.sent(CopyMessage) if c.chat_id != ADMIN_CHAT_ID]


async def show_preview(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, source: Message
) -> Message:
    """`/broadcast` ответом на сообщение — вернуть копию-предпросмотр, как её пришлёт Telegram."""
    await dispatcher.feed_update(bot, broadcast_reply(source))
    [confirm] = to_admins(telegram)
    assert confirm.reply_parameters is not None
    preview_id = confirm.reply_parameters.message_id
    return source.model_copy(update={"message_id": preview_id, "from_user": BOT_USER})


@pytest.mark.usefixtures("audience")
async def test_reply_with_command_shows_copy_preview_and_sends_nothing_to_clients(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await admin_dispatcher.feed_update(bot, broadcast_reply(photo_message()))

    [preview] = telegram.sent(CopyMessage)
    assert (preview.chat_id, preview.from_chat_id, preview.message_id) == (
        ADMIN_CHAT_ID,
        ADMIN_CHAT_ID,
        SOURCE_MESSAGE_ID,
    )
    assert preview.message_thread_id == TOPIC_ID
    assert preview.reply_markup is None  # кнопки — отдельным сообщением, не на копии
    [confirm] = telegram.sent(SendMessage)
    assert confirm.text == content.admin_texts.get(
        "admin.broadcast.copy_header", recipients=2, blocked=1
    )
    assert confirm.message_thread_id == TOPIC_ID
    assert confirm.reply_parameters is not None
    assert confirm.reply_parameters.message_id != SOURCE_MESSAGE_ID  # отвечает на копию
    assert confirm.reply_markup is not None
    assert copies_to_clients(telegram) == []


@pytest.mark.usefixtures("audience")
async def test_photo_with_caption_goes_to_each_client_as_copy_of_preview(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
    remnawave: FakeRemnawave,
) -> None:
    preview = await show_preview(admin_dispatcher, bot, telegram, photo_message())

    await admin_dispatcher.feed_update(bot, confirm_press(SEND_COPY, preview=preview))
    await broadcasts.wait()

    copies = copies_to_clients(telegram)
    assert [c.chat_id for c in copies] == list(BOT_CLIENTS)  # без заблокировавшего и панели
    # ровно то, что было в предпросмотре: копия той самой копии, подпись не подменяем
    assert {(c.from_chat_id, c.message_id) for c in copies} == {(ADMIN_CHAT_ID, preview.message_id)}
    assert {c.caption for c in copies} == {None}
    assert to_clients(telegram) == []  # текстом клиентам ничего не ушло
    assert remnawave.writes == []
    [edit] = telegram.sent(EditMessageReplyMarkup)
    assert edit.message_id == CONFIRM_MESSAGE_ID and edit.reply_markup is None


@pytest.mark.usefixtures("audience")
async def test_copy_double_press_sends_once(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
    content: Content,
) -> None:
    preview = await show_preview(admin_dispatcher, bot, telegram, photo_message())

    await admin_dispatcher.feed_update(bot, confirm_press(SEND_COPY, preview=preview))
    await broadcasts.wait()
    await admin_dispatcher.feed_update(bot, confirm_press(SEND_COPY, preview=preview))
    await broadcasts.wait()

    assert len(copies_to_clients(telegram)) == len(BOT_CLIENTS)
    answers = telegram.sent(AnswerCallbackQuery)
    assert answers[-1].text == content.admin_texts.get("admin.broadcast.already_handled")


@pytest.mark.usefixtures("audience")
async def test_copy_cancel_then_send_sends_nothing(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
) -> None:
    preview = await show_preview(admin_dispatcher, bot, telegram, photo_message())

    await admin_dispatcher.feed_update(bot, confirm_press(CANCEL, preview=preview))
    await admin_dispatcher.feed_update(bot, confirm_press(SEND_COPY, preview=preview))
    await broadcasts.wait()

    assert copies_to_clients(telegram) == []


@pytest.mark.usefixtures("audience")
@pytest.mark.parametrize(
    "preview",
    [
        None,  # копию-предпросмотр удалили — Telegram не присылает, на что был ответ
        photo_message(),  # ответ не на сообщение бота
    ],
)
async def test_send_without_bot_preview_is_refused(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    broadcasts: BroadcastService,
    content: Content,
    preview: Message | None,
) -> None:
    await admin_dispatcher.feed_update(bot, confirm_press(SEND_COPY, preview=preview))
    await broadcasts.wait()

    assert copies_to_clients(telegram) == []
    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.admin_texts.get("admin.broadcast.preview_missing")


@pytest.mark.usefixtures("audience")
async def test_album_is_refused_with_explanation(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    album_photo = photo_message(media_group_id="album-1")

    await admin_dispatcher.feed_update(bot, broadcast_reply(album_photo))

    assert telegram.sent(CopyMessage) == []
    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.admin_texts.get("admin.broadcast.album")


@pytest.mark.usefixtures("audience")
async def test_copy_refused_by_telegram_reports_error_and_offers_nothing(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    telegram.fail_next(CopyMessage, TelegramBadRequest, "Bad Request: message to copy not found")

    await admin_dispatcher.feed_update(bot, broadcast_reply(photo_message()))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.admin_texts.get(
        "admin.broadcast.preview_failed", error="Bad Request: message to copy not found"
    )
    assert sent.reply_markup is None  # отправлять нечего — кнопок нет


async def test_reply_with_text_too_is_refused(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await admin_dispatcher.feed_update(bot, broadcast_reply(photo_message(), "/broadcast Привет"))

    assert telegram.sent(CopyMessage) == []
    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.admin_texts.get("admin.broadcast.reply_or_text")


async def test_bare_command_in_topic_is_not_a_reply(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    # В теме Telegram помечает каждое сообщение ответом на служебное «тема создана»
    topic_created = Message(
        message_id=TOPIC_ID,
        date=DATE,
        chat=ADMIN_GROUP,
        from_user=user(ADMIN_ID, first_name="Админ"),
        forum_topic_created=ForumTopicCreated(name="Рассылки", icon_color=0x6FB9F0),
        message_thread_id=TOPIC_ID,
        is_topic_message=True,
    )

    await admin_dispatcher.feed_update(bot, broadcast_reply(topic_created))

    assert telegram.sent(CopyMessage) == []
    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.admin_texts.get("admin.broadcast.usage")


async def test_sender_copies_message_and_marks_blocked_client(
    bot: Bot,
    ctx: BotContext,
    telegram: FakeTelegramSession,
    clients: ClientService,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all([Client(telegram_id=111), Client(telegram_id=222)])
        await session.commit()
    sender = TelegramBroadcastSender(bot, ctx.delivery)
    content = BroadcastCopy(chat_id=ADMIN_CHAT_ID, message_id=SOURCE_MESSAGE_ID)
    telegram.fail_next(CopyMessage, TelegramForbiddenError, "bot was blocked by the user")

    blocked = await sender.send(111, content)
    delivered = await sender.send(222, content)

    assert (blocked, delivered) == (DeliveryOutcome.BLOCKED, DeliveryOutcome.DELIVERED)
    client = await clients.find(111)
    assert client is not None and client.bot_blocked_at is not None
    copies = telegram.sent(CopyMessage)
    assert [(c.chat_id, c.from_chat_id, c.message_id) for c in copies] == [
        (111, ADMIN_CHAT_ID, SOURCE_MESSAGE_ID),
        (222, ADMIN_CHAT_ID, SOURCE_MESSAGE_ID),
    ]


def plain_message(**content: object) -> Message:
    return Message(message_id=SOURCE_MESSAGE_ID, date=DATE, chat=ADMIN_GROUP, **content)


URL_BUTTONS = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="Сайт", url="https://ts-proxy.com")]]
)


@pytest.mark.parametrize(
    ("source", "refusal"),
    [
        (photo_message(), None),
        (plain_message(text="Просто текст"), None),
        (plain_message(voice=Voice(file_id="v", file_unique_id="vu", duration=3)), None),
        (photo_message(media_group_id="album-1"), "admin.broadcast.album"),
        (
            plain_message(location=Location(latitude=55.7, longitude=37.6)),
            "admin.broadcast.unsupported",
        ),
        (plain_message(dice=Dice(emoji="🎲", value=3)), "admin.broadcast.unsupported"),
        (photo_message(reply_markup=URL_BUTTONS), "admin.broadcast.has_buttons"),
    ],
)
def test_copy_refusal(source: Message, refusal: str | None) -> None:
    assert copy_refusal(source) == refusal


# --- Текст из команды ---


def command_message(text: str, entities: list[MessageEntity]) -> Message:
    return Message(message_id=1, date=DATE, chat=ADMIN_GROUP, text=text, entities=entities)


def test_body_keeps_admin_formatting_after_emoji() -> None:
    # «/broadcast » — 11 единиц UTF-16, «🎉» — 2, пробел — 1: жирное «Скидка» с 14
    text = "/broadcast 🎉 Скидка\nдо пятницы"
    entities = [
        MessageEntity(type="bot_command", offset=0, length=10),
        MessageEntity(type="bold", offset=14, length=6),
        MessageEntity(type="text_link", offset=21, length=2, url="https://ts-proxy.com"),
    ]

    html = command_body_html(command_message(text, entities))

    assert html == '🎉 <b>Скидка</b>\n<a href="https://ts-proxy.com">до</a> пятницы'


def test_body_escapes_plain_text() -> None:
    text = "/broadcast@ts_proxy_test_bot   a < b & c"
    entities = [MessageEntity(type="bot_command", offset=0, length=28)]

    assert command_body_html(command_message(text, entities)) == "a &lt; b &amp; c"


def test_body_missing() -> None:
    entities = [MessageEntity(type="bot_command", offset=0, length=10)]
    assert command_body_html(command_message("/broadcast", entities)) is None


# --- Статистика ---


async def test_stats_command(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await admin_dispatcher.feed_update(
        bot, message(user(ADMIN_ID, first_name="Админ"), "/stats", chat=ADMIN_GROUP)
    )

    [sent] = telegram.sent(SendMessage)
    assert content.admin_texts.get("admin.stats.clients", total=0, blocked=0) in sent.text
    assert content.admin_texts.get("admin.stats.trials_none") in sent.text


def test_duration_is_rendered_in_hours_and_minutes(content: Content) -> None:
    texts = content.admin_texts
    assert format_duration(texts, timedelta(hours=1, minutes=5)) == "1 ч 05 мин"
    assert format_duration(texts, timedelta(minutes=12)) == "12 мин"
    assert format_duration(texts, timedelta(seconds=30)) == "меньше минуты"
