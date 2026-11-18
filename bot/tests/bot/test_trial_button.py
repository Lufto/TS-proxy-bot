"""«Попробовать бесплатно»: ссылка подписки один раз, повтор — «уже использовали»."""

from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    SendMessage,
)
from aiogram.types import InlineKeyboardButton
from bot_updates import ADMIN_CHAT_ID, CLIENT_ID, callback, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_site_banner import banner_alert_button
from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.trial import deliver_pending_trials
from tsproxy.content.dates import client_time
from tsproxy.content.loader import Content
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Order
from tsproxy.domain.constants import TRIAL_DAYS, TRIAL_DEVICE_LIMIT, TRIAL_TRAFFIC_GB
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.fakes import FakeAlertSender, FakeClock

TRIAL = MenuCallback(screen=ScreenId.TRIAL).pack()
MAIN = MenuCallback(screen=ScreenId.MAIN).pack()


def content_granted(ctx: BotContext, expire_at: datetime) -> str:
    return ctx.texts.get(
        "trial.granted",
        expire_at=client_time(expire_at),
        traffic_gb=TRIAL_TRAFFIC_GB,
        device_limit=TRIAL_DEVICE_LIMIT,
    )


def buttons(screen: SendMessage) -> list[InlineKeyboardButton]:
    assert screen.reply_markup is not None
    return [b for row in screen.reply_markup.inline_keyboard for b in row]  # type: ignore[union-attr]


async def test_trial_button_gives_subscription_link(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    clock: FakeClock,
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))

    account = await remnawave.get_account(CLIENT_ID)
    [screen] = telegram.sent(SendMessage)
    assert screen.text == content.texts.get(
        "trial.granted",
        expire_at=client_time(clock.now + timedelta(days=TRIAL_DAYS)),
        traffic_gb=TRIAL_TRAFFIC_GB,
        device_limit=TRIAL_DEVICE_LIMIT,
    )
    assert [b.url for b in buttons(screen) if b.url] == [account.subscription_url]


async def test_second_press_says_trial_already_used(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))
    telegram.requests.clear()

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))

    # Отказ — всплывашкой над кнопкой, экран не меняется
    [answer] = telegram.sent(AnswerCallbackQuery)
    assert (answer.text, answer.show_alert) == (content.texts.get("trial.already_used"), True)
    assert telegram.sent(SendMessage) == []  # нового экрана нет
    assert remnawave.writes == [("create", f"tg_{CLIENT_ID}")]


async def test_refusal_comes_as_message_when_alert_is_too_late(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
) -> None:
    """Панель думала долго, и всплывашку Telegram уже не принял (`query is too old`) —
    отказ приходит сообщением, а не теряется."""
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))
    telegram.requests.clear()
    telegram.fail_next(
        AnswerCallbackQuery,
        TelegramBadRequest,
        "Bad Request: query is too old and response timeout expired or query ID is invalid",
    )

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))

    [refusal] = telegram.sent(SendMessage)
    assert refusal.text == content.texts.get("trial.already_used")
    assert [b.callback_data for b in buttons(refusal)] == [MAIN]


async def test_menu_hides_trial_after_it_was_granted(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))
    telegram.requests.clear()

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), MAIN))

    [screen] = telegram.sent(SendMessage)
    assert content.texts.get("buttons.trial") not in [b.text for b in buttons(screen)]
    async with session_factory() as session:
        client = await session.scalar(select(Client).where(Client.telegram_id == CLIENT_ID))
    assert client is not None and client.trial_granted_at is not None


async def test_paying_client_gets_no_trial(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    clock: FakeClock,
) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=clock.now + timedelta(days=30), device_limit=3, traffic_limit_bytes=0
    )

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))

    [answer] = telegram.sent(AnswerCallbackQuery)
    assert (answer.text, answer.show_alert) == (content.texts.get("trial.has_subscription"), True)
    assert telegram.sent(SendMessage) == []  # нового экрана нет


async def test_panel_down_keeps_trial_and_alerts(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))

    [screen] = telegram.sent(SendMessage)
    assert screen.text == content.texts.get("trial.pending")
    [alert] = alert_sender.sent
    assert alert.chat_id == ADMIN_CHAT_ID
    assert content.admin_texts.get("alert.remnawave_trial.title") in alert.text
    assert "timeout" in alert.text
    assert "tsproxy.bot.trial.on_trial" in alert.text
    unavailable_cause = content.admin_texts.get("failure.remnawave.unavailable.causes.1.cause")
    assert unavailable_cause in alert.text
    assert alert.button == banner_alert_button(content.admin_texts)


async def test_trial_link_arrives_by_itself_when_panel_is_back(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    ctx: BotContext,
    clock: FakeClock,
) -> None:
    """Нажал, панель не ответила — повторно нажимать не нужно: ссылка приходит сама."""
    remnawave.fail_next(RemnawaveUnavailable("timeout"))
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))
    telegram.requests.clear()
    clock.advance(timedelta(minutes=5))

    await deliver_pending_trials(ctx)

    account = await remnawave.get_account(CLIENT_ID)
    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == CLIENT_ID
    assert sent.text == content_granted(ctx, account.expire_at)


async def test_trial_link_sent_by_bot_keeps_its_text_under_new_menu(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    ctx: BotContext,
    clock: FakeClock,
) -> None:
    """Ссылка пробного, присланная ботом сама, — сохраняемый текст (задача
    bot-telegram-ui/25): новое меню гасит только её кнопки."""
    remnawave.fail_next(RemnawaveUnavailable("timeout"))
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))
    clock.advance(timedelta(minutes=5))
    await deliver_pending_trials(ctx)
    link_id = telegram.last_message_id
    telegram.requests.clear()

    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    assert telegram.sent(DeleteMessage) == []
    [stripped] = telegram.sent(EditMessageReplyMarkup)
    assert (stripped.message_id, stripped.reply_markup) == (link_id, None)


async def test_menu_hides_trial_from_paying_client(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
    clock: FakeClock,
) -> None:
    async with session_factory() as session:
        client = Client(telegram_id=CLIENT_ID, first_seen_at=clock.now)
        session.add(client)
        await session.flush()
        session.add(
            Order(
                client_id=client.id,
                tariff_id="month",
                price=Decimal(150),
                days=30,
                status=OrderStatus.ACCESS_GRANTED,
                paid_amount=Decimal(150),
                paid_at=clock.now,
            )
        )
        await session.commit()

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), MAIN))

    [screen] = telegram.sent(SendMessage)
    assert content.texts.get("buttons.trial") not in [b.text for b in buttons(screen)]


async def test_menu_offers_trial_to_client_with_unpaid_order(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
    clock: FakeClock,
) -> None:
    async with session_factory() as session:
        client = Client(telegram_id=CLIENT_ID, first_seen_at=clock.now)
        session.add(client)
        await session.flush()
        session.add(Order(client_id=client.id, tariff_id="month", price=Decimal(150), days=30))
        await session.commit()

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), MAIN))

    [screen] = telegram.sent(SendMessage)
    assert content.texts.get("buttons.trial") in [b.text for b in buttons(screen)]
