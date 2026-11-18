"""Поддержка, задача 17, на подделке Telegram: частые вопросы, карточка клиента, «🙋 Беру»,
«✅ Закрыть», переоткрытие, быстрые ответы, напоминание о забытом обращении."""

from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery,
    CopyMessage,
    CreateForumTopic,
    DeleteMessage,
    EditMessageText,
    SendMessage,
)
from aiogram.types import InlineKeyboardMarkup, Update
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, CLIENT_ID, NOW, callback, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_support_bot import client_text, open_ticket, tickets, topic_text, transcript

from tsproxy.bot.admin_callbacks import AdminExtendCallback
from tsproxy.bot.callbacks import (
    FaqCallback,
    FaqQuestion,
    MenuCallback,
    QuickReplyCallback,
    ScreenId,
    TicketAction,
    TicketCallback,
)
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.support_reminders import SupportReminders
from tsproxy.content.loader import Content, quick_reply_ids
from tsproxy.db.enums import MessageAuthor, TicketStatus
from tsproxy.db.models import Event
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.support import REPLY_REMINDER_AFTER
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock

STRANGER_ID = 555  # не администратор, но состоит в группе
CARD_MESSAGE_ID = 3001  # сообщение бота с кнопками в теме


def buttons(markup: InlineKeyboardMarkup | None) -> list[tuple[str, str | None]]:
    assert markup is not None
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def press(
    action: TicketAction, ticket_id: int, *, by: int = ADMIN_ID, chat_id: int | None = ADMIN_CHAT_ID
) -> Update:
    """Нажатие кнопки под сообщением бота в теме (`chat_id=None` — из личного чата)."""
    data = TicketCallback(action=action, ticket_id=ticket_id).pack()
    return callback(user(by, first_name="Админ"), data, chat_id=chat_id, message_id=CARD_MESSAGE_ID)


def press_quick_reply(ticket_id: int, reply: str, *, by: int = ADMIN_ID) -> Update:
    data = QuickReplyCallback(ticket_id=ticket_id, reply=reply).pack()
    return callback(
        user(by, first_name="Админ"), data, chat_id=ADMIN_CHAT_ID, message_id=CARD_MESSAGE_ID
    )


def topic_messages(telegram: FakeTelegramSession, topic_id: int) -> list[SendMessage]:
    return [
        m
        for m in telegram.sent(SendMessage)
        if m.chat_id == ADMIN_CHAT_ID and m.message_thread_id == topic_id
    ]


def alerts(telegram: FakeTelegramSession) -> list[str | None]:
    return [a.text for a in telegram.sent(AnswerCallbackQuery) if a.show_alert]


async def journal(session_factory: async_sessionmaker[AsyncSession]) -> list[Event]:
    async with session_factory() as session:
        return list(await session.scalars(select(Event).order_by(Event.id)))


async def ticket_id_of(session_factory: async_sessionmaker[AsyncSession]) -> int:
    [ticket] = await tickets(session_factory)
    return ticket.id


# --- Частые вопросы ---


async def test_support_screen_shows_frequent_questions(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    data = MenuCallback(screen=ScreenId.SUPPORT).pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    [screen] = telegram.sent(SendMessage)
    shown = buttons(screen.reply_markup)  # type: ignore[arg-type]
    faq = [(text, data) for text, data in shown if data and data.startswith("faq")]
    assert 3 <= len(faq) - 1 <= 5  # частые вопросы и «✍️ Другой вопрос»
    assert faq[0] == (
        content.texts.get("faq.connect.button"),
        FaqCallback(question=FaqQuestion.CONNECT).pack(),
    )
    assert faq[-1] == (
        content.texts.get("faq.other.button"),
        FaqCallback(question=FaqQuestion.OTHER).pack(),
    )


@pytest.mark.parametrize("question", list(FaqQuestion))
async def test_frequent_question_shows_its_answer(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    question: FaqQuestion,
) -> None:
    data = FaqCallback(question=question).pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    [answer] = telegram.sent(SendMessage)
    assert answer.text == content.texts.get(f"faq.{question.value}.answer")
    back = MenuCallback(screen=ScreenId.SUPPORT).pack()
    assert (content.texts.get("buttons.faq_back"), back) in buttons(answer.reply_markup)  # type: ignore[arg-type]
    assert telegram.sent(CreateForumTopic) == []  # частый вопрос — не обращение


async def test_other_question_opens_ticket_only_after_client_writes(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = FaqCallback(question=FaqQuestion.OTHER).pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))
    assert telegram.sent(CreateForumTopic) == []
    assert await tickets(session_factory) == []

    await dispatcher.feed_update(bot, client_text("Как сменить устройство?"))

    assert len(telegram.sent(CreateForumTopic)) == 1
    assert len(await tickets(session_factory)) == 1


# --- Карточка клиента ---


async def test_topic_starts_with_client_card_and_buttons(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=NOW + timedelta(days=10), device_limit=3, traffic_limit_bytes=0
    )

    topic_id = await open_ticket(dispatcher, bot, session_factory)

    [card] = topic_messages(telegram, topic_id)
    ticket_id = await ticket_id_of(session_factory)
    assert f"Обращение #{ticket_id}" in card.text
    assert "активна до 03.11.2026, 15:00 МСК" in card.text
    assert "Устройства: 0 из 3" in card.text
    assert content.admin_texts.get("support.card.payments_none") in card.text
    assert content.admin_texts.get("support.card.trial_none") in card.text
    actions = [data for _, data in buttons(card.reply_markup)]  # type: ignore[arg-type]
    assert actions == [
        TicketCallback(action=action, ticket_id=ticket_id).pack()
        for action in (
            TicketAction.EXTEND,
            TicketAction.DEVICES,
            TicketAction.REFUND,
            TicketAction.TAKE,
            TicketAction.CLOSE,
            TicketAction.QUICK_REPLIES,
        )
    ]
    # карточка идёт до копии вопроса клиента
    assert telegram.requests.index(card) < telegram.requests.index(telegram.sent(CopyMessage)[0])


async def test_card_without_panel_still_delivers_the_question(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("таймаут"))

    topic_id = await open_ticket(dispatcher, bot, session_factory)

    [card] = topic_messages(telegram, topic_id)
    assert content.admin_texts.get("support.card.subscription_unknown") in card.text
    assert len(telegram.sent(CopyMessage)) == 1


# --- «🙋 Беру» и «✅ Закрыть» ---


async def test_take_shows_who_handles_the_ticket_and_is_logged(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    ticket_id = await ticket_id_of(session_factory)

    await dispatcher.feed_update(bot, press(TicketAction.TAKE, ticket_id))

    [ticket] = await tickets(session_factory)
    assert ticket.assignee_admin_id == ADMIN_ID
    assert topic_messages(telegram, topic_id)[-1].text == content.admin_texts.get(
        "support.taken", admin="Админ"
    )
    [event] = await journal(session_factory)
    assert (event.kind, event.admin_id) == (EventKind.ADMIN_ACTION.value, ADMIN_ID)

    await dispatcher.feed_update(bot, press(TicketAction.TAKE, ticket_id))
    assert alerts(telegram) == [content.admin_texts.get("support.already_yours")]
    assert len(await journal(session_factory)) == 1


@pytest.mark.parametrize(
    ("by", "chat_id"),
    [
        (STRANGER_ID, ADMIN_CHAT_ID),  # не администратор в группе
        (ADMIN_ID, None),  # администратор, но кнопка прислана из лички
    ],
)
async def test_only_admin_in_admin_group_can_press_ticket_buttons(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    by: int,
    chat_id: int | None,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    ticket_id = await ticket_id_of(session_factory)
    before = len(topic_messages(telegram, topic_id))

    for action in TicketAction:
        await dispatcher.feed_update(bot, press(action, ticket_id, by=by, chat_id=chat_id))
    await dispatcher.feed_update(bot, client_reply_attempt(ticket_id, by, chat_id))

    [ticket] = await tickets(session_factory)
    assert (ticket.status, ticket.assignee_admin_id) == (TicketStatus.OPEN, None)
    assert len(topic_messages(telegram, topic_id)) == before
    assert len([m for m in telegram.sent(SendMessage) if m.chat_id == CLIENT_ID]) == 1
    assert await journal(session_factory) == []


def client_reply_attempt(ticket_id: int, by: int, chat_id: int | None) -> Update:
    """Нажатие быстрого ответа — тоже только администратором в группе."""
    data = QuickReplyCallback(ticket_id=ticket_id, reply="greeting").pack()
    return callback(user(by), data, chat_id=chat_id)


async def test_close_then_message_after_23_hours_reopens_in_same_topic(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    content: Content,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    ticket_id = await ticket_id_of(session_factory)

    await dispatcher.feed_update(bot, press(TicketAction.CLOSE, ticket_id))

    [ticket] = await tickets(session_factory)
    assert ticket.status is TicketStatus.CLOSED
    assert topic_messages(telegram, topic_id)[-1].text == content.admin_texts.get(
        "support.closed", admin="Админ"
    )
    await dispatcher.feed_update(bot, press(TicketAction.CLOSE, ticket_id))
    assert alerts(telegram) == [content.admin_texts.get("support.already_closed")]

    clock.advance(timedelta(hours=23))
    await dispatcher.feed_update(bot, client_text("Снова не работает"))

    [ticket] = await tickets(session_factory)
    assert (ticket.id, ticket.status, ticket.topic_id) == (ticket_id, TicketStatus.OPEN, topic_id)
    assert len(telegram.sent(CreateForumTopic)) == 1
    reopen_card = topic_messages(telegram, topic_id)[-1]
    assert "переоткрыто" in reopen_card.text
    assert [c.message_thread_id for c in telegram.sent(CopyMessage)] == [topic_id, topic_id]


async def test_message_25_hours_after_closing_opens_new_ticket_and_topic(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    content: Content,
) -> None:
    first_topic = await open_ticket(dispatcher, bot, session_factory)
    first_id = await ticket_id_of(session_factory)
    await dispatcher.feed_update(bot, press(TicketAction.CLOSE, first_id))
    clock.advance(timedelta(hours=25))

    await dispatcher.feed_update(bot, client_text("Новый вопрос"))

    first, second = await tickets(session_factory)
    assert first.status is TicketStatus.CLOSED
    assert second.topic_id not in (None, first_topic)
    assert len(telegram.sent(CreateForumTopic)) == 2
    client_texts = [m.text for m in telegram.sent(SendMessage) if m.chat_id == CLIENT_ID]
    assert client_texts[-1] == content.texts.get("support.ticket_created", ticket_id=second.id)


async def test_card_buttons_open_admin_actions_for_ticket_client(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    """«➕ Продлить» присылает карточку админки с «➕ 1 / 7 / 30 дн.» (дальше — предпросмотр
    и подтверждение), «📱 Устройства» — устройства клиента этого обращения."""
    await open_ticket(dispatcher, bot, session_factory)
    ticket_id = await ticket_id_of(session_factory)
    telegram.requests.clear()

    await dispatcher.feed_update(bot, press(TicketAction.EXTEND, ticket_id))
    await dispatcher.feed_update(bot, press(TicketAction.DEVICES, ticket_id))
    await dispatcher.feed_update(bot, press(TicketAction.EXTEND, ticket_id + 100))

    card, devices = [m for m in telegram.sent(SendMessage) if m.chat_id == ADMIN_CHAT_ID]
    card_data = [data for _, data in buttons(card.reply_markup)]  # type: ignore[arg-type]
    assert AdminExtendCallback(telegram_id=CLIENT_ID, days=30).pack() in card_data
    assert devices.text == content.admin_texts.get(
        "admin.devices.no_account", telegram_id=CLIENT_ID
    )
    assert alerts(telegram) == [content.admin_texts.get("support.ticket_not_found")]


# --- Быстрые ответы ---


async def test_quick_reply_goes_to_client_and_is_shown_in_topic(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    ticket_id = await ticket_id_of(session_factory)
    reply_id = quick_reply_ids(content.admin_texts)[0]
    reply_text = content.admin_texts.get(f"quick_replies.{reply_id}.text")

    await dispatcher.feed_update(bot, press(TicketAction.QUICK_REPLIES, ticket_id))

    menu = topic_messages(telegram, topic_id)[-1]
    menu_buttons = buttons(menu.reply_markup)  # type: ignore[arg-type]
    assert len(menu_buttons) == len(quick_reply_ids(content.admin_texts)) + 1  # + «Скрыть»
    assert reply_text in menu.text

    await dispatcher.feed_update(bot, press_quick_reply(ticket_id, reply_id))

    to_client = [m for m in telegram.sent(SendMessage) if m.chat_id == CLIENT_ID][-1]
    assert to_client.text == content.texts.get("support.reply_prefix") + reply_text
    [edited] = telegram.sent(EditMessageText)
    assert edited.message_id == CARD_MESSAGE_ID
    assert edited.text.endswith(reply_text) and "Админ" in edited.text
    saved = (await transcript(session_factory))[-1]
    assert (saved.author, saved.text, saved.group_message_id) == (
        MessageAuthor.SUPPORT,
        reply_text,
        CARD_MESSAGE_ID,
    )
    [ticket] = await tickets(session_factory)
    assert ticket.first_response_at is not None
    assert await journal(session_factory) == []  # ответы в журнал не пишем


async def test_quick_reply_menu_can_be_hidden(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await open_ticket(dispatcher, bot, session_factory)

    await dispatcher.feed_update(bot, press(TicketAction.HIDE, await ticket_id_of(session_factory)))

    [deleted] = telegram.sent(DeleteMessage)
    assert (deleted.chat_id, deleted.message_id) == (ADMIN_CHAT_ID, CARD_MESSAGE_ID)


async def test_removed_quick_reply_is_not_sent(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    await open_ticket(dispatcher, bot, session_factory)

    await dispatcher.feed_update(
        bot, press_quick_reply(await ticket_id_of(session_factory), "gone")
    )

    assert alerts(telegram) == [content.admin_texts.get("support.quick_reply_not_found")]
    assert len([m for m in telegram.sent(SendMessage) if m.chat_id == CLIENT_ID]) == 1


async def test_quick_reply_to_client_who_blocked_bot(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    ticket_id = await ticket_id_of(session_factory)
    telegram.fail_next(
        SendMessage, TelegramForbiddenError, "Forbidden: bot was blocked by the user"
    )

    await dispatcher.feed_update(bot, press_quick_reply(ticket_id, "greeting"))

    assert topic_messages(telegram, topic_id)[-1].text == content.admin_texts.get(
        "support.client_blocked"
    )
    saved = (await transcript(session_factory))[-1]
    assert (saved.author, saved.client_message_id) == (MessageAuthor.SUPPORT, None)


# --- Напоминание о забытом обращении ---


async def test_reminder_after_an_hour_without_reply_once_per_wait(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: BotContext,
    clock: FakeClock,
    content: Content,
) -> None:
    reminders = SupportReminders(bot, ctx)
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    asked_at = clock.now
    reminder = content.admin_texts.get("support.reminder", since="24.10.2026, 15:00 МСК")
    assert format_moscow(asked_at) == "24.10.2026, 15:00 МСК"

    clock.advance(REPLY_REMINDER_AFTER)
    await reminders.run()
    assert reminder not in [m.text for m in topic_messages(telegram, topic_id)]

    clock.advance(timedelta(minutes=5))
    await reminders.run()
    await reminders.run()  # второй раз о том же ожидании не напоминаем
    assert [m.text for m in topic_messages(telegram, topic_id)].count(reminder) == 1
    assert clock.now - asked_at > REPLY_REMINDER_AFTER

    # ответили, клиент написал снова и снова ждёт час — новое напоминание
    await dispatcher.feed_update(bot, topic_text("Смотрим", topic_id))
    await dispatcher.feed_update(bot, press(TicketAction.TAKE, await ticket_id_of(session_factory)))
    clock.advance(timedelta(minutes=1))
    await dispatcher.feed_update(bot, client_text("Всё ещё нет"))
    asked_again = clock.now
    clock.advance(timedelta(hours=2))
    await reminders.run()

    last = topic_messages(telegram, topic_id)[-1]
    assert last.text == content.admin_texts.get(
        "support.reminder_assignee", since=format_moscow(asked_again), admin_id=ADMIN_ID
    )
    assert f"tg://user?id={ADMIN_ID}" in last.text


async def test_no_reminder_for_answered_or_closed_tickets(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: BotContext,
    clock: FakeClock,
) -> None:
    topic_id = await open_ticket(dispatcher, bot, session_factory)
    await dispatcher.feed_update(
        bot, press(TicketAction.CLOSE, await ticket_id_of(session_factory))
    )
    before = len(topic_messages(telegram, topic_id))
    clock.advance(timedelta(hours=3))

    await SupportReminders(bot, ctx).run()

    assert len(topic_messages(telegram, topic_id)) == before  # вопрос был, но обращение закрыто
