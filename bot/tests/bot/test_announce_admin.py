"""`/announce` в группе администраторов (задача 35): объявление клиентам в приложении."""

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Update
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_announce import create_admin_announce_router
from tsproxy.bot.context import BotContext
from tsproxy.bot.dispatcher import create_dispatcher
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.throttling import RateLimiter
from tsproxy.content.loader import Content
from tsproxy.db.models import Event
from tsproxy.domain.announcement import ANNOUNCE_MAX_LENGTH, encode_announcement
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveAuthError, RemnawaveUnavailable
from tsproxy.services.announcement import AnnouncementService
from tsproxy.services.events import EventKind

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
STRANGER_ID = 333
TEXT = "Плановые работы 1 ноября с 3:00 до 4:00 МСК"
PROFILE_HEADERS = {"profile-title": "TS Proxy"}


@pytest.fixture
def announce_dispatcher(
    ctx: BotContext,
    limiter: RateLimiter,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
) -> Dispatcher:
    remnawave.subscription_headers.update(PROFILE_HEADERS)
    announcements = AnnouncementService(session_factory, remnawave, ctx.events)
    return create_dispatcher(
        ctx, limiter, extra_routers=[create_admin_announce_router(ctx.access, announcements)]
    )


def announce(text: str, *, sender_id: int = ADMIN_ID, chat: Chat = ADMIN_GROUP) -> Update:
    return message(user(sender_id, first_name="Админ"), text, chat=chat)


async def reply_to(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, update: Update
) -> str:
    await dispatcher.feed_update(bot, update)
    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == ADMIN_CHAT_ID
    telegram.requests.clear()
    return sent.text


async def admin_events(session_factory: async_sessionmaker[AsyncSession]) -> list[Event]:
    async with session_factory() as session:
        query = select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value)
        return list(await session.scalars(query))


async def test_admin_puts_shows_and_removes_announcement(
    announce_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    texts = content.admin_texts

    published = await reply_to(announce_dispatcher, bot, telegram, announce(f"/announce {TEXT}"))

    assert published == texts.get("admin.announce.published", text=TEXT)
    assert remnawave.subscription_headers == {
        **PROFILE_HEADERS,
        "announce": encode_announcement(TEXT),
    }

    shown = await reply_to(announce_dispatcher, bot, telegram, announce("/announce"))
    assert shown == texts.get("admin.announce.current", text=TEXT)

    cleared = await reply_to(announce_dispatcher, bot, telegram, announce("/announce OFF"))
    assert cleared == texts.get("admin.announce.cleared", text=TEXT)
    assert remnawave.subscription_headers == PROFILE_HEADERS

    shown = await reply_to(announce_dispatcher, bot, telegram, announce("/announce"))
    assert shown == texts.get("admin.announce.none")

    actions = [e.details["action"] for e in await admin_events(session_factory) if e.details]
    assert actions == ["announce_set", "announce_cleared"]


async def test_text_keeps_line_breaks(
    announce_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
) -> None:
    await reply_to(announce_dispatcher, bot, telegram, announce("/announce Работы\nскоро вернёмся"))

    assert remnawave.subscription_headers["announce"] == encode_announcement(
        "Работы\nскоро вернёмся"
    )


async def test_too_long_text_is_refused_with_explanation(
    announce_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    remnawave: FakeRemnawave,
) -> None:
    text = "я" * (ANNOUNCE_MAX_LENGTH + 5)

    reply = await reply_to(announce_dispatcher, bot, telegram, announce(f"/announce {text}"))

    assert reply == content.admin_texts.get(
        "admin.announce.too_long", length=ANNOUNCE_MAX_LENGTH + 5, max_length=ANNOUNCE_MAX_LENGTH
    )
    assert remnawave.writes == []


async def test_off_without_announcement(
    announce_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
) -> None:
    reply = await reply_to(announce_dispatcher, bot, telegram, announce("/announce off"))
    assert reply == content.admin_texts.get("admin.announce.already_off")


async def test_panel_down(
    announce_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    remnawave: FakeRemnawave,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("panel down"))

    reply = await reply_to(announce_dispatcher, bot, telegram, announce(f"/announce {TEXT}"))

    assert reply == content.admin_texts.get("admin.panel_unavailable")
    assert "announce" not in remnawave.subscription_headers


async def test_token_without_rights_explains_manual_way(
    announce_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    remnawave: FakeRemnawave,
) -> None:
    remnawave.fail_next(RemnawaveAuthError("HTTP 403"))

    reply = await reply_to(announce_dispatcher, bot, telegram, announce("/announce off"))

    assert reply == content.admin_texts.get("admin.announce.no_rights")


async def test_only_admins_in_admin_group(
    announce_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
) -> None:
    await announce_dispatcher.feed_update(bot, announce(f"/announce {TEXT}", sender_id=STRANGER_ID))
    admin_private = Chat(id=ADMIN_ID, type="private")
    await announce_dispatcher.feed_update(bot, announce(f"/announce {TEXT}", chat=admin_private))

    assert remnawave.writes == []
    assert all(r.chat_id != ADMIN_CHAT_ID for r in telegram.sent(SendMessage))


async def test_usage_mentions_announce(content: Content) -> None:
    assert "/announce" in content.admin_texts.get("admin.usage", max_days=1)
