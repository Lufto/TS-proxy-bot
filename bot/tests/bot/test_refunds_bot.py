"""Возвраты на подделке Telegram (задача 21, решение владельца 2026-10-30): «↩️ Вернуть»
в карточке клиента (тема обращения и /client) и кнопки клиента под сообщением о двойной оплате."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Update
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, CLIENT_ID, callback, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_support_bot import open_ticket, tickets

from tsproxy.bot.admin_callbacks import AdminRefundCallback, AdminTransferCallback
from tsproxy.bot.callbacks import (
    TicketAction,
    TicketCallback,
)
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.domain.constants import PAID_DEVICE_LIMIT, UNLIMITED_TRAFFIC_BYTES
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.payments_types import PaymentRequest
from tsproxy.services.clients import ClientProfile, ClientService
from tsproxy.services.crediting import DOUBLE_DETAIL
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
STRANGER_ID = 333
PRICE = Decimal("150")
MONTH = timedelta(days=30)
CALLBACK_DATA_MAX_BYTES = 64
MAX_TELEGRAM_ID = 2**63 - 1


def press(data: str, *, sender_id: int = ADMIN_ID, chat_id: int | None = ADMIN_CHAT_ID) -> Update:
    return callback(user(sender_id, first_name="Саша"), data, chat_id=chat_id)


def markup_data(markup: object) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b.callback_data or "" for row in markup.inline_keyboard for b in row]


def to_admins(telegram: FakeTelegramSession) -> list[SendMessage]:
    return [m for m in telegram.sent(SendMessage) if m.chat_id == ADMIN_CHAT_ID]


def to_client(telegram: FakeTelegramSession) -> list[SendMessage]:
    return [m for m in telegram.sent(SendMessage) if m.chat_id == CLIENT_ID]


@pytest.fixture
async def bot_client(clients: ClientService) -> None:
    await clients.register(ClientProfile(CLIENT_ID, "Иван", "ivan"))


async def paid_order(
    session_factory: async_sessionmaker[AsyncSession],
    payments: FakePaymentProvider,
    paid_at: datetime,
    expire_at: datetime,
) -> Order:
    """Оплата клиента с выданным доступом: платёж в поддельной платёжке."""
    async with session_factory() as session:
        client = await session.scalar(select(Client).where(Client.telegram_id == CLIENT_ID))
        assert client is not None
        order = Order(client_id=client.id, tariff_id="m1", price=PRICE, days=30)
        session.add(order)
        await session.flush()
        created = await payments.create_payment(
            PaymentRequest(order.id, PRICE, "Подписка", order.idempotency_key)
        )
        payments.ledger.pay(created.payment_id, paid_at)
        order.payment_provider = payments.name
        order.payment_id = created.payment_id
        order.status = OrderStatus.ACCESS_GRANTED
        order.paid_at = paid_at
        order.paid_amount = PRICE
        order.grant_target_expire_at = expire_at
        await session.commit()
    return order


async def account(remnawave: FakeRemnawave, expire_at: datetime) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID,
        expire_at=expire_at,
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )


@pytest.fixture
async def two_payments(
    bot_client: None,
    session_factory: async_sessionmaker[AsyncSession],
    payments: FakePaymentProvider,
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> tuple[Order, Order]:
    """Две оплаты месяца подряд: срок клиента — 60 дней."""
    await account(remnawave, clock() + 2 * MONTH)
    first = await paid_order(session_factory, payments, clock(), clock() + MONTH)
    second = await paid_order(session_factory, payments, clock(), clock() + 2 * MONTH)
    return first, second


# --- Администратор ---


async def test_client_card_has_refund_button(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, bot_client: None
) -> None:
    await dispatcher.feed_update(
        bot, message(user(ADMIN_ID, first_name="Саша"), f"/client {CLIENT_ID}", chat=ADMIN_GROUP)
    )

    [card] = to_admins(telegram)
    assert AdminRefundCallback(telegram_id=CLIENT_ID).pack() in markup_data(card.reply_markup)


async def test_admin_refunds_chosen_payment_and_client_is_told(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    payments: FakePaymentProvider,
    clock: FakeClock,
    content: Content,
    two_payments: tuple[Order, Order],
) -> None:
    _, second = two_payments

    await dispatcher.feed_update(bot, press(AdminRefundCallback(telegram_id=CLIENT_ID).pack()))
    [listing] = to_admins(telegram)
    choose = AdminRefundCallback(telegram_id=CLIENT_ID, order_id=second.id).pack()
    assert choose in markup_data(listing.reply_markup)
    assert f"#{second.id}" in listing.text

    await dispatcher.feed_update(bot, press(choose))
    [_, ask] = to_admins(telegram)
    confirm = AdminRefundCallback(telegram_id=CLIENT_ID, order_id=second.id, confirm=True).pack()
    assert confirm in markup_data(ask.reply_markup)
    assert "150 ₽" in ask.text

    await dispatcher.feed_update(bot, press(confirm))

    [result] = telegram.sent(EditMessageText)
    assert "возвращены" in result.text
    assert (await remnawave.get_account(CLIENT_ID)).expire_at == clock() + MONTH
    [payment] = payments.ledger.find_by_order(second.id)
    assert payment.refunded == PRICE
    [told] = to_client(telegram)
    assert "Возврат 150 ₽ оформлен" in told.text


async def test_second_confirm_does_not_refund_again(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    clock: FakeClock,
    content: Content,
    two_payments: tuple[Order, Order],
) -> None:
    _, second = two_payments
    confirm = AdminRefundCallback(telegram_id=CLIENT_ID, order_id=second.id, confirm=True).pack()

    await dispatcher.feed_update(bot, press(confirm))
    await dispatcher.feed_update(bot, press(confirm))

    [_, again] = telegram.sent(EditMessageText)
    assert again.text == content.admin_texts.get(
        "refunds.refused.already_refunded", order_id=second.id
    )
    assert (await remnawave.get_account(CLIENT_ID)).expire_at == clock() + MONTH


async def test_refund_in_review_is_written_to_topic(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    payments: FakePaymentProvider,
    two_payments: tuple[Order, Order],
) -> None:
    _, second = two_payments
    payments.review_next_refund()

    await dispatcher.feed_update(
        bot,
        press(AdminRefundCallback(telegram_id=CLIENT_ID, order_id=second.id, confirm=True).pack()),
    )

    [result] = telegram.sent(EditMessageText)
    assert "на проверке у платёжки" in result.text
    assert to_client(telegram) == []


async def test_stranger_cannot_refund(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    payments: FakePaymentProvider,
    content: Content,
    two_payments: tuple[Order, Order],
) -> None:
    _, second = two_payments
    confirm = AdminRefundCallback(telegram_id=CLIENT_ID, order_id=second.id, confirm=True).pack()

    await dispatcher.feed_update(bot, press(confirm, sender_id=STRANGER_ID))

    [payment] = payments.ledger.find_by_order(second.id)
    assert payment.refunded == 0
    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.admin_texts.get("admin.not_admin")


async def test_refund_button_in_support_topic_lists_payments(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    two_payments: tuple[Order, Order],
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    [ticket] = await tickets(session_factory)
    data = TicketCallback(action=TicketAction.REFUND, ticket_id=ticket.id).pack()

    await dispatcher.feed_update(bot, press(data))

    *_, listing = to_admins(telegram)
    first, second = two_payments
    assert markup_data(listing.reply_markup) == [
        AdminRefundCallback(telegram_id=CLIENT_ID, order_id=second.id).pack(),
        AdminRefundCallback(telegram_id=CLIENT_ID, order_id=first.id).pack(),
    ]
    assert topic_id  # тема есть — список ушёл рядом с карточкой


# --- Клиент: случайная вторая оплата ---


async def mark_double(
    session_factory: async_sessionmaker[AsyncSession], first: Order, second: Order
) -> None:
    async with session_factory() as session:
        session.add(
            Event(
                kind=EventKind.PAYMENT_RECEIVED.value,
                client_id=second.client_id,
                order_id=second.id,
                details={DOUBLE_DETAIL: first.id},
            )
        )
        await session.commit()


# --- Данные кнопок ---


def test_refund_and_transfer_buttons_fit_telegram_limit() -> None:
    big_order = 10**9
    packed = [
        AdminRefundCallback(telegram_id=MAX_TELEGRAM_ID, order_id=big_order, confirm=True).pack(),
        AdminTransferCallback(
            to_telegram_id=MAX_TELEGRAM_ID,
            order_id=big_order,
            from_stamp=4_102_444_800,  # 2100 год
            to_stamp=4_102_444_800,
        ).pack(),
    ]
    assert all(len(data.encode()) <= CALLBACK_DATA_MAX_BYTES for data in packed)
