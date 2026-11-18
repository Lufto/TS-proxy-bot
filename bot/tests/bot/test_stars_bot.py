"""Оплата звёздами Telegram (задача 33) через диспетчер с подделками: экран тарифа с двумя
способами, счёт, проверка перед оплатой, зачёт, возврат «↩️ Вернуть», /paysupport."""

from itertools import count

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import (
    AnswerPreCheckoutQuery,
    CopyMessage,
    CreateForumTopic,
    RefundStarPayment,
    SendInvoice,
    SendMessage,
)
from aiogram.types import (
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)
from bot_updates import (
    ADMIN_CHAT_ID,
    ADMIN_ID,
    CLIENT_ID,
    DATE,
    OTHER_CLIENT_ID,
    callback,
    message,
    private_chat,
    user,
)
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_callbacks import AdminRefundCallback
from tsproxy.bot.callbacks import (
    MenuCallback,
    ScreenId,
    StarsCallback,
    TariffCallback,
    TariffScreenCallback,
)
from tsproxy.bot.context import BotContext
from tsproxy.bot.dispatcher import bot_commands
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Order
from tsproxy.domain.stars import STARS_CURRENCY
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.telegram_stars import STARS_PROVIDER
from tsproxy.services.fakes import FakeAlertSender
from tsproxy.services.stars import StarsPaid, StarsPayment

CHARGE = "stxCharge-1"
CALLBACK_DATA_MAX_BYTES = 64
INVOICE_TITLE_MAX = 32
INVOICE_DESCRIPTION_MAX = 255
PRE_CHECKOUT_ERROR_MAX = 255
_ids = count(10_000)


def markup_data(markup: object) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b.callback_data or "" for row in markup.inline_keyboard for b in row]


def pre_checkout(
    from_user: User, payload: str, total: int, currency: str = STARS_CURRENCY
) -> Update:
    return Update(
        update_id=next(_ids),
        pre_checkout_query=PreCheckoutQuery(
            id=str(next(_ids)),
            from_user=from_user,
            currency=currency,
            total_amount=total,
            invoice_payload=payload,
        ),
    )


def successful_payment(from_user: User, payload: str, total: int, charge: str = CHARGE) -> Update:
    return Update(
        update_id=next(_ids),
        message=Message(
            message_id=next(_ids),
            date=DATE,
            chat=private_chat(from_user.id),
            from_user=from_user,
            successful_payment=SuccessfulPayment(
                currency=STARS_CURRENCY,
                total_amount=total,
                invoice_payload=payload,
                telegram_payment_charge_id=charge,
                provider_payment_charge_id="",
            ),
        ),
    )


def to_client(telegram: FakeTelegramSession) -> list[SendMessage]:
    return [m for m in telegram.sent(SendMessage) if m.chat_id == CLIENT_ID]


async def orders(session_factory: async_sessionmaker[AsyncSession]) -> list[Order]:
    async with session_factory() as session:
        return list(await session.scalars(select(Order).order_by(Order.id)))


async def ask_invoice(dispatcher: Dispatcher, bot: Bot, tariff_id: str = "m1") -> None:
    data = StarsCallback(tariff_id=tariff_id).pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))


async def pay_month(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, charge: str = CHARGE
) -> str:
    """Клиент берёт счёт за месяц и платит 100 ⭐. Возвращает payload счёта."""
    await ask_invoice(dispatcher, bot)
    payload = telegram.sent(SendInvoice)[-1].payload
    telegram.requests.clear()  # дальше — только то, что бот ответил на оплату
    await dispatcher.feed_update(bot, pre_checkout(user(CLIENT_ID), payload, 100))
    await dispatcher.feed_update(bot, successful_payment(user(CLIENT_ID), payload, 100, charge))
    return payload


# --- Экран тарифа: два способа оплаты ---


async def test_tariff_screen_offers_sbp_and_stars(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    data = TariffScreenCallback(tariff_id="m3").pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    screen = telegram.sent(SendMessage)[-1]
    assert "405 ₽" in screen.text and "270 ⭐" in screen.text
    buttons = markup_data(screen.reply_markup)
    assert buttons[:2] == [
        TariffCallback(tariff_id="m3").pack(),
        StarsCallback(tariff_id="m3").pack(),
    ]
    assert MenuCallback(screen=ScreenId.TARIFFS).pack() in buttons


async def test_stars_button_for_removed_test_tariff_is_refused(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ask_invoice(dispatcher, bot, "test15")

    assert telegram.sent(SendInvoice) == []
    assert await orders(session_factory) == []


# --- Счёт ---


async def test_stars_button_sends_invoice_in_stars(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    session_factory: async_sessionmaker[AsyncSession],
    payments: FakePaymentProvider,
) -> None:
    await ask_invoice(dispatcher, bot, "m3")

    [order] = await orders(session_factory)
    [invoice] = telegram.sent(SendInvoice)
    assert (invoice.chat_id, invoice.currency, invoice.provider_token) == (
        CLIENT_ID,
        STARS_CURRENCY,
        "",
    )
    assert invoice.payload == str(order.id)
    assert [price.amount for price in invoice.prices] == [270]
    assert len(invoice.title) <= INVOICE_TITLE_MAX
    assert len(invoice.description) <= INVOICE_DESCRIPTION_MAX
    # из счёта можно уйти не платя: первой — оплата (так требует Telegram), под ней — назад
    assert isinstance(invoice.reply_markup, InlineKeyboardMarkup)
    pay, *back = [row[0] for row in invoice.reply_markup.inline_keyboard]
    assert pay.pay is True and "270" in pay.text
    assert [b.callback_data for b in back] == [
        MenuCallback(screen=ScreenId.TARIFFS).pack(),
        MenuCallback(screen=ScreenId.MAIN).pack(),
    ]
    assert telegram.sent(SendMessage)[-1].text == content.texts.get("stars.invoice_sent", stars=270)
    assert order.payment_provider == STARS_PROVIDER
    assert payments.ledger.all() == []


# --- Проверка перед оплатой ---


async def test_pre_checkout_is_answered_ok(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await ask_invoice(dispatcher, bot)
    payload = telegram.sent(SendInvoice)[0].payload

    await dispatcher.feed_update(bot, pre_checkout(user(CLIENT_ID), payload, 100))

    [answer] = telegram.sent(AnswerPreCheckoutQuery)
    assert answer.ok is True


async def test_pre_checkout_with_other_amount_is_refused_with_text(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await ask_invoice(dispatcher, bot)
    payload = telegram.sent(SendInvoice)[0].payload

    await dispatcher.feed_update(bot, pre_checkout(user(CLIENT_ID), payload, 1))

    [answer] = telegram.sent(AnswerPreCheckoutQuery)
    assert answer.ok is False
    assert answer.error_message == content.texts.get("stars.refused.amount_mismatch")


async def test_forwarded_invoice_cannot_be_paid_by_other(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await ask_invoice(dispatcher, bot)
    payload = telegram.sent(SendInvoice)[0].payload

    await dispatcher.feed_update(bot, pre_checkout(user(OTHER_CLIENT_ID), payload, 100))

    [answer] = telegram.sent(AnswerPreCheckoutQuery)
    assert answer.ok is False
    assert answer.error_message == content.texts.get("stars.refused.not_found")


def test_refusal_texts_fit_telegram(content: Content) -> None:
    for key in ("not_found", "already_paid", "expired", "amount_mismatch", "error"):
        assert len(content.texts.get(f"stars.refused.{key}")) <= PRE_CHECKOUT_ERROR_MAX


# --- Оплата ---


async def test_payment_grants_access_and_tells_admins(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    alert_sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await pay_month(dispatcher, bot, telegram)

    [order] = await orders(session_factory)
    assert (order.status, order.payment_id) == (OrderStatus.ACCESS_GRANTED, CHARGE)
    [paid] = to_client(telegram)
    assert "✅" in paid.text
    [posted] = [m for m in alert_sender.sent if "⭐" in m.text]
    assert "100 ⭐" in posted.text
    # сообщение об оплате не ушло в поддержку как обращение
    assert telegram.sent(CreateForumTopic) == [] and telegram.sent(CopyMessage) == []


async def test_repeated_payment_update_is_credited_once(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    payload = await pay_month(dispatcher, bot, telegram)

    await dispatcher.feed_update(bot, successful_payment(user(CLIENT_ID), payload, 100))

    assert len(to_client(telegram)) == 1
    assert telegram.sent(RefundStarPayment) == []


async def test_payment_that_fits_no_order_is_returned(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, successful_payment(user(CLIENT_ID), "999", 100))

    [refund] = telegram.sent(RefundStarPayment)
    assert (refund.user_id, refund.telegram_payment_charge_id) == (CLIENT_ID, CHARGE)
    [told] = to_client(telegram)
    assert told.text == content.texts.get("stars.returned", stars=100)


# --- Возврат «↩️ Вернуть» ---


async def test_admin_refund_returns_stars(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await pay_month(dispatcher, bot, telegram)
    [order] = await orders(session_factory)
    admin = user(ADMIN_ID, first_name="Саша")

    choose = AdminRefundCallback(telegram_id=CLIENT_ID, order_id=order.id).pack()
    await dispatcher.feed_update(bot, callback(admin, choose, chat_id=ADMIN_CHAT_ID))
    ask = [m for m in telegram.sent(SendMessage) if m.chat_id == ADMIN_CHAT_ID][-1]
    assert "100 ⭐" in ask.text
    confirm = AdminRefundCallback(telegram_id=CLIENT_ID, order_id=order.id, confirm=True).pack()
    await dispatcher.feed_update(bot, callback(admin, confirm, chat_id=ADMIN_CHAT_ID))

    [refund] = telegram.sent(RefundStarPayment)
    assert (refund.user_id, refund.telegram_payment_charge_id) == (CLIENT_ID, CHARGE)
    told = to_client(telegram)[-1]
    assert "Возврат 100 ⭐ оформлен" in told.text
    [refunded] = await orders(session_factory)
    assert refunded.status is OrderStatus.REFUNDED


async def test_refund_router_is_wired_for_stars(ctx: BotContext) -> None:
    # тот же сервис возвратов, что у кнопки администратора «↩️ Вернуть»
    assert ctx.refunds._refunds.supports(STARS_PROVIDER)


# --- /paysupport ---


async def test_paysupport_opens_support(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, ctx: BotContext
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/paysupport"))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == ctx.screens.support().text


def test_paysupport_is_in_command_menu(content: Content) -> None:
    assert "paysupport" in [command.command for command in bot_commands(content.texts)]


def test_new_buttons_fit_telegram_limit() -> None:
    for data in (
        TariffScreenCallback(tariff_id="m12").pack(),
        StarsCallback(tariff_id="m12").pack(),
    ):
        assert len(data.encode()) <= CALLBACK_DATA_MAX_BYTES


def test_bot_asks_telegram_for_payment_updates(dispatcher: Dispatcher) -> None:
    # webhook и polling просят у Telegram только те апдейты, на которые есть обработчики
    assert {"pre_checkout_query", "message"} <= set(dispatcher.resolve_used_update_types())


async def test_payment_lost_to_database_failure_names_the_charge(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    alert_sender: FakeAlertSender,
    ctx: BotContext,
    content: Content,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Telegram не пришлёт оплату второй раз: тревога должна назвать платёж, а клиент — знать,
    # что звёзды не пропали
    async def database_down(payment: StarsPayment) -> StarsPaid:
        raise OperationalError("UPDATE orders", {}, Exception("база не отвечает"))

    await ask_invoice(dispatcher, bot)
    payload = telegram.sent(SendInvoice)[-1].payload
    telegram.requests.clear()  # дальше — только то, что бот ответил на оплату
    monkeypatch.setattr(ctx.stars, "paid", database_down)

    await dispatcher.feed_update(bot, successful_payment(user(CLIENT_ID), payload, 100))

    [alert] = [m for m in alert_sender.sent if CHARGE in m.text]
    assert "100 ⭐" in alert.text and str(CLIENT_ID) in alert.text
    [told] = to_client(telegram)
    assert told.text == content.texts.get("stars.unrecorded", stars=100)
    assert telegram.sent(RefundStarPayment) == []
