"""«Тарифы», `/buy` и экран оплаты (задача 12) — через диспетчер с подделками."""

from decimal import Decimal

from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import InlineKeyboardMarkup
from bot_updates import ADMIN_ID, BOT_TOKEN, CLIENT_ID, NOW, callback, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import (
    MenuCallback,
    ScreenId,
    StarsCallback,
    TariffCallback,
    TariffScreenCallback,
)
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.tariffs import TariffScreens
from tsproxy.content.loader import REQUIRED_TEXTS, Content, Texts
from tsproxy.db.models import Client, Order
from tsproxy.domain.constants import ADMIN_TEST_TARIFF_ID
from tsproxy.integrations.failures import Failure
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.payments_types import (
    PaymentFailure,
    PaymentRejected,
    PaymentUnavailable,
)
from tsproxy.services.fakes import FakeAlertSender
from tsproxy.services.orders import NEW_PAYMENTS_LIMIT, Checkout, TariffOffer
from tsproxy.services.payment_checks import CheckOutcome

CALLBACK_ANSWER_MAX = 200
CALLBACK_DATA_MAX_BYTES = 64
ANY_MENU = MenuCallback(screen=ScreenId.MAIN).pack()


def buttons(markup: InlineKeyboardMarkup | None) -> list[tuple[str, str | None, str | None]]:
    assert markup is not None
    return [(b.text, b.callback_data, b.url) for row in markup.inline_keyboard for b in row]


def buy(tariff_id: str) -> str:
    """«💳 СБП» на экране тарифа (и старые кнопки тарифов)."""
    return TariffCallback(tariff_id=tariff_id).pack()


def tariff_screen(tariff_id: str) -> str:
    """Тариф в списке — экран тарифа со способами оплаты (задача 33)."""
    return TariffScreenCallback(tariff_id=tariff_id).pack()


def last_screen(telegram: FakeTelegramSession) -> SendMessage:
    return telegram.sent(SendMessage)[-1]


async def orders_in_db(session_factory: async_sessionmaker[AsyncSession]) -> list[Order]:
    async with session_factory() as session:
        return list(await session.scalars(select(Order).order_by(Order.id)))


async def test_client_sees_tariffs_with_discount(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    data = MenuCallback(screen=ScreenId.TARIFFS).pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    edited = last_screen(telegram)
    assert edited.text == content.texts.get("tariffs.title")
    shown = buttons(edited.reply_markup)  # type: ignore[arg-type]
    assert [d for _, d, _ in shown] == [
        tariff_screen("m1"),
        tariff_screen("m3"),
        tariff_screen("m6"),
        tariff_screen("m12"),
        ANY_MENU,
    ]
    assert shown[0][0] == "1 месяц — 150 ₽"
    assert shown[1][0] == "3 месяца — 405 ₽ · выгода 10%"


async def test_only_admin_sees_test_tariff(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """Тестовый тариф за 50 ₽ (владелец, 2026-11-20) — только у администратора, последним.
    Старого тестового за 15 ₽ (задача 15) нет ни у кого."""
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/buy"))
    await dispatcher.feed_update(bot, message(user(ADMIN_ID), "/buy"))

    client_screen, admin_screen = telegram.sent(SendMessage)
    client_data = [d for _, d, _ in buttons(client_screen.reply_markup)]  # type: ignore[arg-type]
    admin_shown = buttons(admin_screen.reply_markup)  # type: ignore[arg-type]
    assert tariff_screen(ADMIN_TEST_TARIFF_ID) not in client_data
    assert admin_shown[-2] == ("Тест для админов — 50 ₽", tariff_screen(ADMIN_TEST_TARIFF_ID), None)
    assert tariff_screen("test15") not in [d for _, d, _ in admin_shown]


async def test_admin_test_tariff_costs_50_rubles_or_one_star(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await dispatcher.feed_update(bot, callback(user(ADMIN_ID), tariff_screen(ADMIN_TEST_TARIFF_ID)))

    shown = [text for text, _, _ in buttons(last_screen(telegram).reply_markup)]  # type: ignore[arg-type]
    assert "СБП — 50 ₽" in shown
    assert "Stars — 1 ⭐" in shown


async def test_client_forged_test_tariff_button_orders_nothing(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    payments: FakePaymentProvider,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    """Клиент прислал кнопку тестового тарифа (подделка): отказ и тарифы, без заказа."""
    for data in (
        tariff_screen(ADMIN_TEST_TARIFF_ID),
        buy(ADMIN_TEST_TARIFF_ID),
        StarsCallback(tariff_id=ADMIN_TEST_TARIFF_ID).pack(),
    ):
        await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    assert await orders_in_db(session_factory) == []
    assert payments.ledger.all() == []
    refusals = [a.text for a in telegram.sent(AnswerCallbackQuery) if a.text]
    assert refusals == [content.texts.get("tariffs.not_available")] * 3


async def test_buy_command_opens_tariffs(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/buy"))

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.texts.get("tariffs.title")
    async with session_factory() as session:
        assert await session.scalar(select(Client).where(Client.telegram_id == CLIENT_ID))


async def test_tariff_press_shows_payment_link(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    payments: FakePaymentProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("m3")))

    [order] = await orders_in_db(session_factory)
    assert (order.tariff_id, order.price) == ("m3", Decimal("405"))
    edited = last_screen(telegram)
    assert "405 ₽" in edited.text
    assert "24.10.2026, 16:00 МСК" in edited.text  # 12:00 UTC + час = 16:00 по Москве
    [pay, *_] = buttons(edited.reply_markup)  # type: ignore[arg-type]
    assert pay[2] == order.payment_url
    assert len(payments.ledger.all()) == 1


async def test_double_press_gives_one_payment_other_tariff_new_one(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    payments: FakePaymentProvider,
) -> None:
    """«Готово, когда» задачи 12."""
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("m1")))
    first = last_screen(telegram).reply_markup
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("m1")))
    second = last_screen(telegram).reply_markup

    assert buttons(first)[0] == buttons(second)[0]  # type: ignore[arg-type]
    assert len(payments.ledger.all()) == 1

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("m6")))
    assert len(payments.ledger.all()) == 2


async def test_old_test_tariff_button_is_refused(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("test15")))

    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.texts.get("tariffs.not_available")
    assert last_screen(telegram).text == content.texts.get("tariffs.title")
    assert payments.ledger.all() == []
    assert await orders_in_db(session_factory) == []


async def test_rejected_keys_alert_shows_platega_answer_masked(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    alert_sender: FakeAlertSender,
) -> None:
    answer = f"HTTP 401, Auth:GEN_1001, Access denied {BOT_TOKEN}"
    payments.fail_next(
        PaymentRejected(
            f"Platega: {answer}",
            failure=Failure(PaymentFailure.KEYS_REJECTED, {"error": answer}),
        )
    )

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("m1")))

    assert last_screen(telegram).text == content.texts.get("payment.unavailable")
    [alert] = alert_sender.sent
    texts = content.admin_texts
    assert texts.get("alert.payment_create_rejected.title") in alert.text
    assert "Auth:GEN_1001, Access denied" in alert.text
    assert BOT_TOKEN not in alert.text
    assert texts.get("failure.platega.keys_rejected.causes.1.cause") in alert.text


async def test_payments_down_apologizes_and_alerts(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    alert_sender: FakeAlertSender,
) -> None:
    payments.fail_next(PaymentUnavailable("таймаут"))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("m1")))

    assert last_screen(telegram).text == content.texts.get("payment.unavailable")
    [alert] = alert_sender.sent
    texts = content.admin_texts
    assert texts.get("alert.payments_unavailable.title") in alert.text
    assert "таймаут" in alert.text
    assert "tsproxy.bot.tariffs.refusal_screen" in alert.text
    assert texts.get("failure.payments.unavailable.causes.1.cause") in alert.text


async def test_payment_refused_alerts_with_provider_text(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    alert_sender: FakeAlertSender,
) -> None:
    """Задача 15: Platega отказала создать платёж (например, потребовала email) — тревога
    с её текстом, клиенту — «платёжный сервис не отвечает»."""
    payments.fail_next(PaymentRejected("Platega: HTTP 400, metadata.user_email required"))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("m1")))

    assert last_screen(telegram).text == content.texts.get("payment.unavailable")
    [alert] = alert_sender.sent
    assert "metadata.user_email required" in alert.text


async def test_too_many_new_payments(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    payments: FakePaymentProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        client = Client(telegram_id=CLIENT_ID, first_seen_at=NOW)
        session.add(client)
        await session.flush()
        for _ in range(NEW_PAYMENTS_LIMIT):
            session.add(
                Order(client_id=client.id, tariff_id="m12", price=1, days=1, created_at=NOW)
            )
        await session.commit()

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), buy("m1")))

    assert last_screen(telegram).text == content.texts.get("payment.too_many")
    assert payments.ledger.all() == []


async def test_screens_use_only_required_texts(content: Content) -> None:
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    screens = TariffScreens(texts)
    offers = tuple(TariffOffer(t, 10) for t in content.tariffs)
    screens.tariffs(offers)
    screens.tariff(content.tariffs[0], 100)  # задача 33: экран тарифа, СБП и звёзды
    screens.tariff(content.tariffs[0], None)
    screens.stars_sent(content.tariffs[0].id, 100)
    screens.payment(Checkout(1, content.tariffs[0], "https://pay.example/1", NOW, reused=False))
    screens.notice("payment.too_many")
    screens.notice("payment.unavailable")
    for outcome in CheckOutcome:  # задача 14: ответы на «Проверить оплату»
        screens.check_result(outcome, 1)
    screens.notice("payment.found")
    screens.notice("payment.check_unavailable", check_order_id=1)
    assert texts.get("payment.description", title="1 месяц")


def test_limits_of_telegram(content: Content) -> None:
    assert len(content.texts.get("tariffs.not_available")) <= CALLBACK_ANSWER_MAX
    for tariff in content.tariffs:
        assert len(buy(tariff.id).encode()) <= CALLBACK_DATA_MAX_BYTES
        assert len(tariff_screen(tariff.id).encode()) <= CALLBACK_DATA_MAX_BYTES
