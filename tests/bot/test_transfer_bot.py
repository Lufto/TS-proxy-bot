"""`/transfer` на подделке Telegram (задача 21): перенос подписки на другой аккаунт только по
подтверждённому платежу — предпросмотр, «✅ Да, перенести», сообщение новому аккаунту."""

from datetime import timedelta
from decimal import Decimal

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Update
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, CLIENT_ID, OTHER_CLIENT_ID, callback, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_transfer import create_admin_transfer_router
from tsproxy.bot.context import BotContext
from tsproxy.bot.dispatcher import create_dispatcher
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.throttling import RateLimiter
from tsproxy.content.loader import Content
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Order
from tsproxy.domain.constants import PAID_DEVICE_LIMIT, UNLIMITED_TRAFFIC_BYTES
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus
from tsproxy.services.clients import ClientProfile, ClientService
from tsproxy.services.fakes import FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.transfer import TransferService

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
STRANGER_ID = 333
PAYMENT_ID = "fake_payment_1"


@pytest.fixture
def transfer_dispatcher(
    ctx: BotContext,
    limiter: RateLimiter,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    content: Content,
    clock: FakeClock,
) -> Dispatcher:
    service = TransferService(
        session_factory,
        remnawave,
        ctx.events,
        ctx.alerter,
        content.admin_texts,
        clock,
        KeyedLocks(),
    )
    return create_dispatcher(
        ctx, limiter, extra_routers=[create_admin_transfer_router(ctx.access, service)]
    )


@pytest.fixture
async def paid_source(
    clients: ClientService,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    """Старый аккаунт оплатил месяц (платёж PAYMENT_ID), новый написал боту."""
    await clients.register(ClientProfile(CLIENT_ID, "Иван", "ivan"))
    await clients.register(ClientProfile(OTHER_CLIENT_ID, "Иван", "ivan_new"))
    async with session_factory() as session:
        client = await session.scalar(select(Client).where(Client.telegram_id == CLIENT_ID))
        assert client is not None
        session.add(
            Order(
                client_id=client.id,
                tariff_id="m1",
                price=Decimal(150),
                days=30,
                status=OrderStatus.ACCESS_GRANTED,
                payment_provider="fake",
                payment_id=PAYMENT_ID,
                paid_at=clock(),
                paid_amount=Decimal(150),
                grant_target_expire_at=clock() + timedelta(days=20),
            )
        )
        await session.commit()
    await remnawave.get_or_create_account(
        CLIENT_ID,
        expire_at=clock() + timedelta(days=20),
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )


def command(text: str, *, sender_id: int = ADMIN_ID) -> Update:
    return message(user(sender_id, first_name="Саша"), text, chat=ADMIN_GROUP)


async def test_transfer_with_preview_and_confirmation(
    transfer_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    clock: FakeClock,
    paid_source: None,
) -> None:
    await transfer_dispatcher.feed_update(
        bot, command(f"/transfer {CLIENT_ID} {OTHER_CLIENT_ID} {PAYMENT_ID}")
    )

    [preview] = [m for m in telegram.sent(SendMessage) if m.chat_id == ADMIN_CHAT_ID]
    assert "Перенос подписки" in preview.text
    assert isinstance(preview.reply_markup, InlineKeyboardMarkup)
    confirm = preview.reply_markup.inline_keyboard[0][0].callback_data
    assert confirm is not None

    await transfer_dispatcher.feed_update(
        bot, callback(user(ADMIN_ID, first_name="Саша"), confirm, chat_id=ADMIN_CHAT_ID)
    )

    [done] = telegram.sent(EditMessageText)
    assert "Подписка перенесена" in done.text
    assert (await remnawave.get_account(OTHER_CLIENT_ID)).expire_at == clock() + timedelta(days=20)
    assert (await remnawave.get_account(CLIENT_ID)).status is AccountStatus.DISABLED
    [told] = [m for m in telegram.sent(SendMessage) if m.chat_id == OTHER_CLIENT_ID]
    assert "перенесена на этот аккаунт" in told.text


async def test_unconfirmed_payment_is_refused(
    transfer_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    paid_source: None,
) -> None:
    await transfer_dispatcher.feed_update(
        bot, command(f"/transfer {CLIENT_ID} {OTHER_CLIENT_ID} fake_other")
    )

    [reply] = telegram.sent(SendMessage)
    assert reply.text == content.admin_texts.get(
        "admin.transfer.payment_not_found", from_id=CLIENT_ID, payment_id="fake_other"
    )
    assert reply.reply_markup is None


async def test_bad_arguments_show_usage(
    transfer_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await transfer_dispatcher.feed_update(bot, command("/transfer @ivan 222"))

    [reply] = telegram.sent(SendMessage)
    assert reply.text == content.admin_texts.get("admin.transfer.usage")


async def test_stranger_cannot_transfer(
    transfer_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    paid_source: None,
) -> None:
    await transfer_dispatcher.feed_update(
        bot,
        command(f"/transfer {CLIENT_ID} {OTHER_CLIENT_ID} {PAYMENT_ID}", sender_id=STRANGER_ID),
    )

    assert [m for m in telegram.sent(SendMessage) if m.chat_id == ADMIN_CHAT_ID] == []
