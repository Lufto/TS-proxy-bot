"""Чужие чаты (аудит R19), «клиент заблокировал бота», доступ администраторов."""

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import LeaveChat, SendMessage
from aiogram.types import CallbackQuery, Chat
from bot_updates import (
    ADMIN_CHAT_ID,
    ADMIN_ID,
    BOT_ID,
    CLIENT_ID,
    DATE,
    NOW,
    bot_status_changed,
    message,
    private_chat,
    user,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.db.models import Client
from tsproxy.services.clients import ClientService
from tsproxy.services.fakes import FakeClock

FOREIGN_GROUP = Chat(id=-100777, type=ChatType.SUPERGROUP, title="Чужая группа")
ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type=ChatType.SUPERGROUP, title="Лично не публично")


async def blocked_at(session_factory: async_sessionmaker[AsyncSession]) -> object:
    async with session_factory() as session:
        return await session.scalar(
            select(Client.bot_blocked_at).where(Client.telegram_id == CLIENT_ID)
        )


async def add_client(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=CLIENT_ID, first_seen_at=NOW))
        await session.commit()


async def test_bot_leaves_foreign_group(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    update = bot_status_changed(FOREIGN_GROUP, user(CLIENT_ID), bot_id=BOT_ID, present=True)
    await dispatcher.feed_update(bot, update)

    [leave] = telegram.sent(LeaveChat)
    assert leave.chat_id == FOREIGN_GROUP.id


async def test_bot_leaves_foreign_group_on_any_message(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start", chat=FOREIGN_GROUP))

    assert [leave.chat_id for leave in telegram.sent(LeaveChat)] == [FOREIGN_GROUP.id]
    assert telegram.sent(SendMessage) == []  # меню в чужой группе не показываем


async def test_bot_leaves_foreign_channel(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    channel = Chat(id=-100888, type=ChatType.CHANNEL, title="Канал")
    update = bot_status_changed(channel, user(CLIENT_ID), bot_id=BOT_ID, present=True)
    await dispatcher.feed_update(bot, update)

    assert [leave.chat_id for leave in telegram.sent(LeaveChat)] == [channel.id]


async def test_bot_stays_in_admin_group(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(
        bot, bot_status_changed(ADMIN_GROUP, user(ADMIN_ID), bot_id=BOT_ID, present=True)
    )
    await dispatcher.feed_update(bot, message(user(ADMIN_ID), "привет", chat=ADMIN_GROUP))

    assert telegram.requests == []


async def test_client_blocking_bot_sets_mark(
    dispatcher: Dispatcher,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await add_client(session_factory)

    update = bot_status_changed(
        private_chat(CLIENT_ID), user(CLIENT_ID), bot_id=BOT_ID, present=False
    )
    await dispatcher.feed_update(bot, update)

    assert await blocked_at(session_factory) == NOW


async def test_forbidden_on_send_sets_blocked_mark(
    ctx: BotContext,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await add_client(session_factory)
    telegram.fail_next(
        SendMessage, TelegramForbiddenError, "Forbidden: bot was blocked by the user"
    )

    result = await ctx.delivery.send(CLIENT_ID, ctx.screens.instructions_menu())

    assert result is None
    assert await blocked_at(session_factory) == NOW


async def test_blocked_mark_keeps_first_time(
    clients: ClientService, clock: FakeClock, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await add_client(session_factory)
    assert await clients.mark_blocked(CLIENT_ID) is True
    clock.now = DATE.replace(year=2027)
    assert await clients.mark_blocked(CLIENT_ID) is False
    assert await blocked_at(session_factory) == NOW


async def test_successful_send_keeps_client_unmarked(
    ctx: BotContext, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await add_client(session_factory)
    assert await ctx.delivery.send(CLIENT_ID, ctx.screens.instructions_menu()) is not None
    assert await blocked_at(session_factory) is None


async def test_admin_filter_requires_admin_in_admin_chat() -> None:
    access = AdminAccess(frozenset({ADMIN_ID}), ADMIN_CHAT_ID)
    admin_filter = AdminFilter(access)

    def from_(user_id: int, chat: Chat) -> object:
        update = message(user(user_id), "/stats", chat=chat)
        return update.message

    assert await admin_filter(from_(ADMIN_ID, ADMIN_GROUP)) is True  # type: ignore[arg-type]
    assert await admin_filter(from_(CLIENT_ID, ADMIN_GROUP)) is False  # type: ignore[arg-type]
    # администратор, но в личке или чужой группе — не админка
    assert await admin_filter(from_(ADMIN_ID, private_chat(ADMIN_ID))) is False  # type: ignore[arg-type]
    assert await admin_filter(from_(ADMIN_ID, FOREIGN_GROUP)) is False  # type: ignore[arg-type]
    button = CallbackQuery(
        id="1",
        from_user=user(CLIENT_ID),
        chat_instance="ci",
        data="admin:anything",
        message=message(user(ADMIN_ID), "карточка", chat=ADMIN_GROUP).message,
    )
    assert await admin_filter(button) is False  # подделанная кнопка от не-администратора
