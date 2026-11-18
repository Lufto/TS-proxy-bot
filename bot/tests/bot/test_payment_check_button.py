"""«Проверить оплату» и «Купить» после потерянного уведомления (задача 14) — через диспетчер."""

from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardMarkup
from bot_updates import CLIENT_ID, OTHER_CLIENT_ID, callback, user
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import PaymentCheckCallback, TariffCallback
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Order
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.payments_types import PaymentUnavailable
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.payment_checks import CheckOutcome


def check(order_id: int) -> str:
    return PaymentCheckCallback(order_id=order_id).pack()


def callback_data(markup: InlineKeyboardMarkup | None) -> list[str | None]:
    assert markup is not None
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def only_order(session_factory: async_sessionmaker[AsyncSession]) -> Order:
    async with session_factory() as session:
        [order] = list(await session.scalars(select(Order)))
        return order


async def buy_month(dispatcher: Dispatcher, bot: Bot, user_id: int = CLIENT_ID) -> None:
    await dispatcher.feed_update(
        bot, callback(user(user_id), TariffCallback(tariff_id="m1").pack())
    )


async def test_payment_screen_has_check_button(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await buy_month(dispatcher, bot)

    order = await only_order(session_factory)
    screen = telegram.sent(SendMessage)[-1]
    assert check(order.id) in callback_data(screen.reply_markup)  # type: ignore[arg-type]
    assert "«Проверить оплату»" in screen.text


async def test_check_before_paying_says_not_yet(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await buy_month(dispatcher, bot)
    order = await only_order(session_factory)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), check(order.id)))

    screen = telegram.sent(SendMessage)[-1]
    assert screen.text == content.texts.get("payment.check_not_paid")
    assert check(order.id) in callback_data(screen.reply_markup)  # type: ignore[arg-type]


async def test_check_after_paying_credits_and_sends_paid_message(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await buy_month(dispatcher, bot)
    order = await only_order(session_factory)
    assert order.payment_id is not None
    payments.ledger.pay(order.payment_id, clock())

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), check(order.id)))

    *_, paid, found = [m for m in telegram.sent(SendMessage) if m.chat_id == CLIENT_ID]
    assert paid.text.startswith("<b>✅ Оплачено, подписка до")
    assert found.text == content.texts.get("payment.found")
    assert (await only_order(session_factory)).status is OrderStatus.ACCESS_GRANTED


async def test_forged_check_of_other_clients_order_finds_nothing(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await buy_month(dispatcher, bot, OTHER_CLIENT_ID)
    order = await only_order(session_factory)
    assert order.payment_id is not None
    payments.ledger.pay(order.payment_id, clock())

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), check(order.id)))

    assert telegram.sent(SendMessage)[-1].text == content.texts.get("payment.check_not_found")
    assert (await only_order(session_factory)).status is OrderStatus.CREATED


async def test_check_with_provider_down_apologizes_and_alerts(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    alert_sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await buy_month(dispatcher, bot)
    order = await only_order(session_factory)
    payments.fail_next(PaymentUnavailable("таймаут"))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), check(order.id)))

    assert telegram.sent(SendMessage)[-1].text == content.texts.get("payment.check_unavailable")
    [alert] = alert_sender.sent
    texts = content.admin_texts
    assert texts.get("alert.payment_check_button_unavailable.title") in alert.text
    assert "таймаут" in alert.text
    assert (
        texts.get("alert.payment_check_button_unavailable.where", order_id=order.id) in alert.text
    )
    assert "tsproxy.bot.tariffs.on_payment_check" in alert.text
    assert texts.get("failure.payments.unavailable.causes.1.cause") in alert.text


async def test_buy_after_lost_notification_finds_payment_instead_of_new_link(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Уязвимость 1: оплатил, уведомление потерялось, через час снова «Купить»."""
    await buy_month(dispatcher, bot)
    order = await only_order(session_factory)
    assert order.payment_id is not None
    payments.ledger.pay(order.payment_id, clock())
    clock.advance(timedelta(minutes=61))

    await buy_month(dispatcher, bot)

    assert telegram.sent(SendMessage)[-1].text == content.texts.get("payment.found")
    assert len(payments.ledger.all()) == 1
    assert (await only_order(session_factory)).status is OrderStatus.ACCESS_GRANTED


async def test_buy_with_provider_silent_on_check_gives_no_link(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
) -> None:
    await buy_month(dispatcher, bot)
    payments.fail_next(PaymentUnavailable("таймаут"))

    await dispatcher.feed_update(
        bot, callback(user(CLIENT_ID), TariffCallback(tariff_id="m3").pack())
    )

    assert telegram.sent(SendMessage)[-1].text == content.texts.get("payment.unavailable")
    assert len(payments.ledger.all()) == 1


async def test_check_when_payment_not_recorded_says_admin_will_grant(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    ctx: BotContext,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # оплата есть, а база её не записала (тревогу поднимает зачёт): клиент не гадает,
    # что случилось, — доступ выдаст администратор
    async def database_down(client_id: int, order_id: int) -> CheckOutcome:
        raise OperationalError("UPDATE orders", {}, Exception("database is locked"))

    await buy_month(dispatcher, bot)
    order = await only_order(session_factory)
    monkeypatch.setattr(ctx.payment_checks, "check_order", database_down)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), check(order.id)))

    assert telegram.sent(SendMessage)[-1].text == content.texts.get("payment.not_recorded")
