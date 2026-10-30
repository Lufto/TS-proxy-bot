"""Компенсация при сбое в Telegram (задача 20): предложение в «🚨 Тревоги», подтверждение,
ход и итог, извинение клиенту (и в льготный период); двойное «Да» не начисляет дважды,
чужое нажатие — отказ; `/outages` — список сбоев с кнопками."""

from collections import Counter
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Update
from bot_updates import (
    ADMIN_CHAT_ID,
    ADMIN_ID,
    ALERTS_THREAD_ID,
    CLIENT_ID,
    NOW,
    callback,
    message,
    user,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_compensation import (
    CompensationButtons,
    CompensationCallback,
    CompensationScreens,
    CompensationStep,
    TelegramApologyMessenger,
    TelegramCompensationReporter,
    create_admin_compensation_router,
    duration_words,
)
from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext
from tsproxy.bot.dispatcher import create_dispatcher
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.throttling import RateLimiter
from tsproxy.content.loader import Content
from tsproxy.db.models import Client, NodeOutage
from tsproxy.domain.compensation import CompensationAction, Outage
from tsproxy.domain.constants import PAID_DEVICE_LIMIT, UNLIMITED_TRAFFIC_BYTES
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.services.compensation import (
    Apology,
    ClientOutcome,
    CompensationReport,
    CompensationRun,
    CompensationService,
)
from tsproxy.services.fakes import FakeClock
from tsproxy.services.locks import KeyedLocks

STRANGER_ID = 333
ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Админы", is_forum=True)
EXPIRE = NOW + timedelta(days=10)
# простой 2 ч 10 мин → 3 часа + 1 день
OUTAGE_START = NOW - timedelta(hours=3)
OUTAGE_END = OUTAGE_START + timedelta(hours=2, minutes=10)
AMOUNT = timedelta(days=1, hours=3)


@pytest.fixture
def screens(content: Content) -> CompensationScreens:
    return CompensationScreens(content.admin_texts, content.texts)


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    ctx: BotContext,
    bot: Bot,
    content: Content,
    clock: FakeClock,
    screens: CompensationScreens,
) -> CompensationService:
    return CompensationService(
        session_factory,
        remnawave,
        ctx.events,
        ctx.alerter,
        content.admin_texts,
        TelegramCompensationReporter(bot, screens, ADMIN_CHAT_ID, ALERTS_THREAD_ID),
        TelegramApologyMessenger(ctx.delivery, content.texts),
        clock,
        KeyedLocks(),
    )


@pytest.fixture
def admin_dispatcher(
    ctx: BotContext,
    limiter: RateLimiter,
    service: CompensationService,
    screens: CompensationScreens,
    content: Content,
) -> Dispatcher:
    buttons = CompensationButtons(service, screens, content.admin_texts)
    return create_dispatcher(
        ctx, limiter, extra_routers=[create_admin_compensation_router(ctx.access, buttons)]
    )


@pytest.fixture
async def outage_id(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        outage = NodeOutage(node_uuid="node-1", started_at=OUTAGE_START, ended_at=OUTAGE_END)
        session.add(outage)
        await session.commit()
        return outage.id


@pytest.fixture
async def paid_client(
    session_factory: async_sessionmaker[AsyncSession], remnawave: FakeRemnawave
) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=CLIENT_ID, first_seen_at=NOW))
        await session.commit()
    await remnawave.get_or_create_account(
        CLIENT_ID,
        expire_at=EXPIRE,
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )


def press(outage_id: int, step: CompensationStep, *, sender_id: int = ADMIN_ID) -> Update:
    data = CompensationCallback(outage_id=outage_id, step=step).pack()
    return callback(user(sender_id, first_name="Админ"), data, chat_id=ADMIN_CHAT_ID)


def buttons_of(markup: object) -> list[str | None]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def step_data(outage_id: int, step: CompensationStep) -> str:
    return CompensationCallback(outage_id=outage_id, step=step).pack()


def test_amount_in_words(content: Content) -> None:
    assert duration_words(content.texts, timedelta(days=1, hours=3)) == "1 день 3 часа"
    assert duration_words(content.texts, timedelta(days=2)) == "2 дня"
    assert duration_words(content.texts, timedelta(hours=5)) == "5 часов"


async def test_restored_node_posts_offer_with_button(
    service: CompensationService, telegram: FakeTelegramSession, outage_id: int
) -> None:
    await service.outage_closed(outage_id)

    [offer] = telegram.sent(SendMessage)
    assert offer.chat_id == ADMIN_CHAT_ID
    assert offer.message_thread_id == ALERTS_THREAD_ID
    assert "130 мин" in offer.text
    assert "1 день 3 часа" in offer.text
    assert buttons_of(offer.reply_markup) == [step_data(outage_id, CompensationStep.ASK)]


@pytest.mark.usefixtures("paid_client")
async def test_compensation_needs_confirmation_then_runs_once(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    service: CompensationService,
    remnawave: FakeRemnawave,
    outage_id: int,
) -> None:
    await admin_dispatcher.feed_update(bot, press(outage_id, CompensationStep.ASK))

    [confirm] = telegram.sent(EditMessageText)
    assert buttons_of(confirm.reply_markup) == [
        step_data(outage_id, CompensationStep.CONFIRM),
        step_data(outage_id, CompensationStep.CANCEL),
    ]
    assert (await remnawave.get_account(CLIENT_ID)).expire_at == EXPIRE  # без «Да» — ничего
    telegram.requests.clear()

    await admin_dispatcher.feed_update(bot, press(outage_id, CompensationStep.CONFIRM))
    await service.drain()
    await admin_dispatcher.feed_update(bot, press(outage_id, CompensationStep.CONFIRM))
    await service.drain()

    assert (await remnawave.get_account(CLIENT_ID)).expire_at == EXPIRE + AMOUNT
    sent = telegram.sent(SendMessage)
    apologies = [message for message in sent if message.chat_id == CLIENT_ID]
    assert len(apologies) == 1  # второе «Да» продолжило тот же прогон — дважды не начислено
    assert "1 день 3 часа" in apologies[0].text
    assert "04.11.2026, 18:00 МСК" in apologies[0].text
    reports = [m.text for m in sent if m.chat_id == ADMIN_CHAT_ID and "закончена" in m.text]
    assert len(reports) == 2
    assert "Продлено: 1" in reports[0]
    assert "Уже было начислено раньше: 1" in reports[1]
    in_progress = telegram.sent(EditMessageText)[0]
    assert buttons_of(in_progress.reply_markup) == [step_data(outage_id, CompensationStep.RESUME)]


async def test_cancel_returns_offer(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, outage_id: int
) -> None:
    await admin_dispatcher.feed_update(bot, press(outage_id, CompensationStep.CANCEL))

    [edited] = telegram.sent(EditMessageText)
    assert buttons_of(edited.reply_markup) == [step_data(outage_id, CompensationStep.ASK)]


@pytest.mark.usefixtures("paid_client")
async def test_stranger_cannot_compensate(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    service: CompensationService,
    remnawave: FakeRemnawave,
    outage_id: int,
) -> None:
    await admin_dispatcher.feed_update(
        bot, press(outage_id, CompensationStep.CONFIRM, sender_id=STRANGER_ID)
    )
    await service.drain()

    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.show_alert
    assert (await remnawave.get_account(CLIENT_ID)).expire_at == EXPIRE
    assert telegram.sent(SendMessage) == []


async def test_unknown_outage_answers_not_found(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await admin_dispatcher.feed_update(bot, press(4242, CompensationStep.CONFIRM))

    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.admin_texts.get("admin.compensation.not_found")


# --- Льготный период: извинение и итог ---


def test_grace_apology_names_new_grace_end_and_offers_renewal(
    ctx: BotContext, content: Content
) -> None:
    messenger = TelegramApologyMessenger(ctx.delivery, content.texts)

    screen = messenger.screen(Apology(CompensationAction.EXTEND_GRACE, AMOUNT, EXPIRE))

    assert "1 день 3 часа" in screen.text
    assert "03.11.2026, 15:00 МСК" in screen.text
    assert buttons_of(screen.markup) == [MenuCallback(screen=ScreenId.TARIFFS).pack()]


def test_report_counts_grace_extensions(screens: CompensationScreens) -> None:
    run = CompensationRun(1, ADMIN_ID, 1, AMOUNT, Outage(OUTAGE_START, OUTAGE_END))
    counts = Counter({ClientOutcome.EXTENDED: 2, ClientOutcome.GRACE_EXTENDED: 1})

    report = screens.finished(CompensationReport(run, total=3, counts=counts))

    assert "Продлено: 2" in report.text
    assert "Продлён льготный период: 1" in report.text


# --- /outages ---


def outages_command(*, sender_id: int = ADMIN_ID) -> Update:
    return message(user(sender_id, first_name="Админ"), "/outages", chat=ADMIN_GROUP)


async def test_outages_without_outages(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await admin_dispatcher.feed_update(bot, outages_command())

    [sent] = telegram.sent(SendMessage)
    assert sent.text == content.admin_texts.get("admin.outages.empty")


async def test_outages_lists_outages_with_compensate_button(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    outage_id: int,
) -> None:
    async with session_factory() as session:
        session.add(NodeOutage(node_uuid="node-2", started_at=NOW - timedelta(minutes=5)))
        await session.commit()

    await admin_dispatcher.feed_update(bot, outages_command())

    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == ADMIN_CHAT_ID
    assert "130 мин" in sent.text
    assert "1 день 3 часа" in sent.text
    assert "ещё не восстановилась" in sent.text
    # кнопка — только у восстановившейся и не компенсированной ноды
    assert buttons_of(sent.reply_markup) == [step_data(outage_id, CompensationStep.ASK)]


@pytest.mark.usefixtures("paid_client")
async def test_outages_offers_resume_after_compensation(
    admin_dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    service: CompensationService,
    outage_id: int,
) -> None:
    await admin_dispatcher.feed_update(bot, press(outage_id, CompensationStep.CONFIRM))
    await service.drain()
    telegram.requests.clear()

    await admin_dispatcher.feed_update(bot, outages_command())

    [sent] = telegram.sent(SendMessage)
    assert "Компенсировано" in sent.text
    assert buttons_of(sent.reply_markup) == [step_data(outage_id, CompensationStep.RESUME)]


async def test_outages_ask_button_leads_to_confirmation(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, outage_id: int
) -> None:
    await admin_dispatcher.feed_update(bot, outages_command())
    await admin_dispatcher.feed_update(bot, press(outage_id, CompensationStep.ASK))

    [confirm] = telegram.sent(EditMessageText)
    assert buttons_of(confirm.reply_markup) == [
        step_data(outage_id, CompensationStep.CONFIRM),
        step_data(outage_id, CompensationStep.CANCEL),
    ]


async def test_outages_ignores_strangers(
    admin_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, outage_id: int
) -> None:
    await admin_dispatcher.feed_update(bot, outages_command(sender_id=STRANGER_ID))
    assert telegram.sent(SendMessage) == []
