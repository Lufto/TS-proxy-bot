"""Админка на подделке Telegram (задача 18): права, карточка клиента, выдача, сброс пробного,
устройства и перевыпуск ссылки. Главное — не-администратор ничего не может."""

from datetime import datetime, timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Message, Update
from bot_updates import (
    ADMIN_CHAT_ID,
    ADMIN_ID,
    CLIENT_ID,
    DATE,
    OTHER_CLIENT_ID,
    callback,
    message,
    user,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_callbacks import (
    AdminAction,
    AdminClientCallback,
    AdminDeviceCallback,
    AdminExtendCallback,
    expire_stamp,
)
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import REQUIRED_ADMIN_TEXTS, Content
from tsproxy.db.models import Event
from tsproxy.domain.constants import (
    MAX_MANUAL_GRANT_DAYS,
    PAID_DEVICE_LIMIT,
    UNLIMITED_TRAFFIC_BYTES,
)
from tsproxy.domain.dates import format_moscow
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import Device, RemnawaveAuthError, RemnawaveUnavailable
from tsproxy.services.clients import ClientProfile, ClientService
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock
from tsproxy.services.profile import device_ref

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
OTHER_GROUP_ID = -100777
STRANGER_ID = 333  # участник группы, но не администратор
SUPPORT_TOPIC_ID = 55
CALLBACK_DATA_MAX_BYTES = 64
CALLBACK_ANSWER_MAX = 200
MAX_TELEGRAM_ID = 2**63 - 1


def group_command(text: str, *, sender_id: int = ADMIN_ID, topic_id: int | None = None) -> Update:
    if topic_id is None:
        return message(user(sender_id, first_name="Админ"), text, chat=ADMIN_GROUP)
    update = message(user(sender_id, first_name="Админ"), text, chat=ADMIN_GROUP)
    assert update.message is not None
    topic_message = update.message.model_copy(
        update={"message_thread_id": topic_id, "is_topic_message": True}
    )
    return update.model_copy(update={"message": topic_message})


def press(data: str, *, sender_id: int = ADMIN_ID, chat_id: int = ADMIN_CHAT_ID) -> Update:
    return callback(user(sender_id, first_name="Админ"), data, chat_id=chat_id)


def sent_to(telegram: FakeTelegramSession, chat_id: int) -> list[SendMessage]:
    return [m for m in telegram.sent(SendMessage) if m.chat_id == chat_id]


def last_markup(telegram: FakeTelegramSession) -> InlineKeyboardMarkup:
    [*_, last] = sent_to(telegram, ADMIN_CHAT_ID)
    assert isinstance(last.reply_markup, InlineKeyboardMarkup)
    return last.reply_markup


def button_data(markup: InlineKeyboardMarkup, prefix: str) -> list[str]:
    return [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data is not None and b.callback_data.startswith(prefix)
    ]


async def admin_events(session_factory: async_sessionmaker[AsyncSession]) -> list[Event]:
    async with session_factory() as session:
        query = select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value)
        return list(await session.scalars(query))


@pytest.fixture
async def bot_client(clients: ClientService) -> None:
    await clients.register(ClientProfile(CLIENT_ID, "Иван", "ivan"))


async def paid_account(remnawave: FakeRemnawave, expire_at: datetime) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID,
        expire_at=expire_at,
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )


def extend_confirm(days: int, expire_at: datetime | None) -> str:
    return AdminExtendCallback(
        telegram_id=CLIENT_ID, days=days, confirm=True, stamp=expire_stamp(expire_at)
    ).pack()


def client_action(action: AdminAction) -> str:
    return AdminClientCallback(action=action, telegram_id=CLIENT_ID).pack()


# --- Не-администратор ничего не может ---


async def test_stranger_in_admin_group_gets_no_card(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, bot_client: None
) -> None:
    await dispatcher.feed_update(bot, group_command(f"/client {CLIENT_ID}", sender_id=STRANGER_ID))
    await dispatcher.feed_update(bot, group_command(f"/give {CLIENT_ID} 30", sender_id=STRANGER_ID))
    await dispatcher.feed_update(bot, group_command("/help", sender_id=STRANGER_ID))

    assert telegram.sent(SendMessage) == []


async def test_help_in_admin_group_lists_admin_commands(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, group_command("/help"))

    [reply] = sent_to(telegram, ADMIN_CHAT_ID)
    assert reply.text == content.admin_texts.get("admin.usage", max_days=MAX_MANUAL_GRANT_DAYS)


async def test_admin_commands_do_nothing_in_private_chat(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    bot_client: None,
) -> None:
    admin = user(ADMIN_ID, first_name="Админ")
    await dispatcher.feed_update(bot, message(admin, f"/give {CLIENT_ID} 30"))

    [reply] = telegram.sent(SendMessage)
    assert reply.chat_id == ADMIN_ID
    assert reply.text == content.texts.get("fallback.unknown_command")
    assert remnawave.writes == []


@pytest.mark.parametrize(
    "data",
    [
        extend_confirm(30, None),
        client_action(AdminAction.RESET_TRIAL),
        client_action(AdminAction.REVOKE),
        client_action(AdminAction.DEVICES),
        AdminDeviceCallback(telegram_id=CLIENT_ID, ref=device_ref("a")).pack(),
    ],
)
@pytest.mark.parametrize(
    ("sender_id", "chat_id"),
    [(STRANGER_ID, ADMIN_CHAT_ID), (ADMIN_ID, OTHER_GROUP_ID), (ADMIN_ID, ADMIN_ID)],
    ids=["stranger_in_group", "admin_in_other_group", "admin_in_private"],
)
async def test_admin_buttons_refused_without_rights(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
    bot_client: None,
    clock: FakeClock,
    data: str,
    sender_id: int,
    chat_id: int,
) -> None:
    """Кнопку можно подделать: права проверяются на каждом нажатии, данные кнопки — не права."""
    await paid_account(remnawave, clock.now + timedelta(days=3))
    remnawave.writes.clear()

    await dispatcher.feed_update(bot, press(data, sender_id=sender_id, chat_id=chat_id))

    assert remnawave.writes == []
    assert telegram.sent(SendMessage) == []
    assert telegram.sent(EditMessageText) == []
    assert await admin_events(session_factory) == []
    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.admin_texts.get("admin.not_admin")
    assert answer.show_alert


# --- Карточка ---


async def test_card_by_id_in_support_topic(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    bot_client: None,
    clock: FakeClock,
) -> None:
    expire_at = clock.now + timedelta(days=20)
    await paid_account(remnawave, expire_at)

    await dispatcher.feed_update(
        bot, group_command(f"/client {CLIENT_ID}", topic_id=SUPPORT_TOPIC_ID)
    )

    [card] = sent_to(telegram, ADMIN_CHAT_ID)
    assert card.message_thread_id == SUPPORT_TOPIC_ID  # ответ в той же теме
    assert str(CLIENT_ID) in card.text
    assert format_moscow(expire_at) in card.text
    assert isinstance(card.reply_markup, InlineKeyboardMarkup)
    assert button_data(card.reply_markup, "admx:")  # кнопки «➕ N дн.»


async def test_card_by_username(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, bot_client: None
) -> None:
    await dispatcher.feed_update(bot, group_command("/client @IVAN"))

    [card] = sent_to(telegram, ADMIN_CHAT_ID)
    assert str(CLIENT_ID) in card.text


async def test_same_username_lists_ids(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    clients: ClientService,
    bot_client: None,
) -> None:
    await clients.register(ClientProfile(OTHER_CLIENT_ID, "Пётр", "ivan"))

    await dispatcher.feed_update(bot, group_command("/client @ivan"))

    [reply] = sent_to(telegram, ADMIN_CHAT_ID)
    assert f"/client {CLIENT_ID}" in reply.text and f"/client {OTHER_CLIENT_ID}" in reply.text


@pytest.mark.parametrize("text", ["/client", "/client кто-то", f"/client {OTHER_CLIENT_ID}"])
async def test_bad_or_unknown_query(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    bot_client: None,
    text: str,
) -> None:
    await dispatcher.feed_update(bot, group_command(text))

    [reply] = sent_to(telegram, ADMIN_CHAT_ID)
    assert str(CLIENT_ID) not in reply.text


async def test_card_when_panel_is_down(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    bot_client: None,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    await dispatcher.feed_update(bot, group_command(f"/client {CLIENT_ID}"))

    [card] = sent_to(telegram, ADMIN_CHAT_ID)
    assert content.admin_texts.get("admin.card.panel_unavailable") in card.text


# --- Ручная выдача ---


async def test_give_previews_then_extends_once(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    bot_client: None,
    clock: FakeClock,
) -> None:
    expire_at = clock.now + timedelta(days=10)
    await paid_account(remnawave, expire_at)
    new_expire = expire_at + timedelta(days=30)

    await dispatcher.feed_update(bot, group_command(f"/give {CLIENT_ID} 30"))

    [preview] = sent_to(telegram, ADMIN_CHAT_ID)
    assert format_moscow(new_expire) in preview.text
    assert (await remnawave.get_account(CLIENT_ID)).expire_at == expire_at  # пока не выдано
    [confirm] = button_data(last_markup(telegram), "admx:")

    await dispatcher.feed_update(bot, press(confirm))
    await dispatcher.feed_update(bot, press(confirm))  # двойное нажатие

    assert (await remnawave.get_account(CLIENT_ID)).expire_at == new_expire
    done, changed = telegram.sent(EditMessageText)
    assert format_moscow(new_expire) in done.text
    assert done.reply_markup is None  # кнопку «Да» больше не нажать
    assert format_moscow(new_expire) in changed.text
    [event] = await admin_events(session_factory)
    assert event.admin_id == ADMIN_ID


async def test_extend_tells_client_once(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    bot_client: None,
    clock: FakeClock,
) -> None:
    expire_at = clock.now + timedelta(days=10)
    await paid_account(remnawave, expire_at)
    await dispatcher.feed_update(bot, group_command(f"/give {CLIENT_ID} 30"))
    [confirm] = button_data(last_markup(telegram), "admx:")

    await dispatcher.feed_update(bot, press(confirm))
    await dispatcher.feed_update(bot, press(confirm))  # двойное нажатие

    [told] = sent_to(telegram, CLIENT_ID)
    assert "30 дней" in told.text
    assert format_moscow(expire_at + timedelta(days=30)) in told.text


async def test_extend_button_from_card_opens_preview(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    bot_client: None,
    clock: FakeClock,
) -> None:
    await dispatcher.feed_update(bot, group_command(f"/client {CLIENT_ID}"))
    first_preset = button_data(last_markup(telegram), "admx:")[0]

    await dispatcher.feed_update(bot, press(first_preset))

    assert remnawave.writes == []  # только предпросмотр
    [confirm] = button_data(last_markup(telegram), "admx:")
    await dispatcher.feed_update(bot, press(confirm))
    assert (await remnawave.get_account(CLIENT_ID)).expire_at > clock.now


@pytest.mark.parametrize("days", ["0", str(MAX_MANUAL_GRANT_DAYS + 1)])
async def test_give_over_limit_refused(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    bot_client: None,
    days: str,
) -> None:
    await dispatcher.feed_update(bot, group_command(f"/give {CLIENT_ID} {days}"))

    [reply] = sent_to(telegram, ADMIN_CHAT_ID)
    expected = content.admin_texts.get("admin.days_out_of_range", max_days=MAX_MANUAL_GRANT_DAYS)
    assert reply.text == expected
    assert reply.reply_markup is None
    assert remnawave.writes == []


@pytest.mark.parametrize("text", ["/give", "/give @ivan 30", f"/give {CLIENT_ID}", "/give 1 2 3"])
async def test_give_needs_id_and_days(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    bot_client: None,
    text: str,
) -> None:
    """Выдача — только по Telegram ID: @username мог смениться (аудит R22)."""
    await dispatcher.feed_update(bot, group_command(text))

    [reply] = sent_to(telegram, ADMIN_CHAT_ID)
    expected = content.admin_texts.get("admin.give_usage", max_days=MAX_MANUAL_GRANT_DAYS)
    assert reply.text == expected


async def test_forged_extend_over_limit_matches_no_handler(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    """Кнопку с 9999 днями не собрать: данные проверяет pydantic, обработчика для них нет."""
    data = f"admx:{CLIENT_ID}:9999:1:0"

    await dispatcher.feed_update(bot, press(data))

    assert remnawave.writes == []


# --- Сброс пробного, устройства, ссылка ---


async def test_reset_trial_with_confirmation(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
    bot_client: None,
) -> None:
    client_user = user(CLIENT_ID)
    await dispatcher.feed_update(bot, callback(client_user, "menu:trial", message_id=1))
    telegram.requests.clear()

    await dispatcher.feed_update(bot, press(client_action(AdminAction.RESET_TRIAL_ASK)))
    assert await admin_events(session_factory) == []  # пока только вопрос
    [confirm] = button_data(last_markup(telegram), "adm:trial:")
    await dispatcher.feed_update(bot, press(confirm))

    [done] = telegram.sent(EditMessageText)
    assert done.text == content.admin_texts.get("admin.reset.done", telegram_id=CLIENT_ID)
    assert len(await admin_events(session_factory)) == 1


async def test_cancel_does_nothing(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
    bot_client: None,
    clock: FakeClock,
) -> None:
    await paid_account(remnawave, clock.now + timedelta(days=3))
    await dispatcher.feed_update(bot, press(client_action(AdminAction.REVOKE_ASK)))
    [cancel] = button_data(last_markup(telegram), "adm:cancel:")

    await dispatcher.feed_update(bot, press(cancel))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.admin_texts.get("admin.cancelled")
    assert remnawave.writes == [("create", f"tg_{CLIENT_ID}")]
    assert await admin_events(session_factory) == []


async def test_revoke_link(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    bot_client: None,
    clock: FakeClock,
) -> None:
    await paid_account(remnawave, clock.now + timedelta(days=3))
    before = (await remnawave.get_account(CLIENT_ID)).subscription_url

    await dispatcher.feed_update(bot, press(client_action(AdminAction.REVOKE)))

    assert (await remnawave.get_account(CLIENT_ID)).subscription_url != before


async def test_devices_list_and_delete(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    bot_client: None,
    clock: FakeClock,
) -> None:
    await paid_account(remnawave, clock.now + timedelta(days=3))
    for hwid in ("a", "b"):
        remnawave.simulate_device(
            CLIENT_ID,
            Device(
                hwid=hwid, platform="iOS", os_version="18", device_model="iPhone", created_at=DATE
            ),
        )

    await dispatcher.feed_update(bot, press(client_action(AdminAction.DEVICES)))
    first, _ = button_data(last_markup(telegram), "admd:")
    await dispatcher.feed_update(bot, press(first))

    assert [d.hwid for d in await remnawave.list_devices(CLIENT_ID)] == ["b"]
    [edited] = telegram.sent(EditMessageText)
    assert content.admin_texts.get("admin.devices.deleted") in edited.text


async def test_panel_down_on_confirm_keeps_question(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    bot_client: None,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    await dispatcher.feed_update(bot, press(extend_confirm(7, None)))

    [reply] = sent_to(telegram, ADMIN_CHAT_ID)
    assert reply.text.startswith("⚠️ Не вышло")
    assert "Почему: панель не ответила: timeout" in reply.text
    unavailable_cause = content.admin_texts.get("failure.remnawave.unavailable.causes.1.cause")
    assert f"Что делать: {unavailable_cause}" in reply.text
    assert telegram.sent(EditMessageText) == []


async def test_rejected_token_on_confirm_says_why_and_what_to_do(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
    bot_client: None,
) -> None:
    """Раньше «токен не принят» уходил в «Ошибку в боте» — теперь это понятное предупреждение."""
    remnawave.fail_next(RemnawaveAuthError("Панель не приняла токен: HTTP 401"))

    await dispatcher.feed_update(bot, press(extend_confirm(7, None)))

    [reply] = sent_to(telegram, ADMIN_CHAT_ID)
    assert "HTTP 401" in reply.text
    token_cause = content.admin_texts.get("failure.remnawave.token_rejected.causes.1.cause")
    assert token_cause in reply.text


# --- Тексты и данные кнопок ---


def test_admin_callback_data_fits_telegram_limit() -> None:
    packed = [
        AdminExtendCallback(
            telegram_id=MAX_TELEGRAM_ID,
            days=MAX_MANUAL_GRANT_DAYS,
            confirm=True,
            stamp=expire_stamp(datetime(2099, 12, 31, tzinfo=DATE.tzinfo)),
        ).pack(),
        AdminDeviceCallback(telegram_id=MAX_TELEGRAM_ID, ref=device_ref("x")).pack(),
        *(
            AdminClientCallback(action=action, telegram_id=MAX_TELEGRAM_ID).pack()
            for action in AdminAction
        ),
    ]
    assert all(len(data.encode()) <= CALLBACK_DATA_MAX_BYTES for data in packed)


def test_admin_texts_present_and_short_enough(content: Content) -> None:
    admin_keys = {key for key in REQUIRED_ADMIN_TEXTS if key.startswith("admin.")}
    assert "admin.card.title" in admin_keys
    assert len(content.admin_texts.get("admin.not_admin")) <= CALLBACK_ANSWER_MAX


async def test_reply_to_button_goes_to_same_topic(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, bot_client: None
) -> None:
    """Кнопка под сообщением в теме: ответ — в ту же тему."""
    update = press(client_action(AdminAction.RESET_TRIAL_ASK))
    assert update.callback_query is not None
    in_topic = Message(
        message_id=5,
        date=DATE,
        chat=ADMIN_GROUP,
        text="карточка",
        message_thread_id=SUPPORT_TOPIC_ID,
        is_topic_message=True,
    )
    query = update.callback_query.model_copy(update={"message": in_topic})

    await dispatcher.feed_update(bot, update.model_copy(update={"callback_query": query}))

    [ask] = sent_to(telegram, ADMIN_CHAT_ID)
    assert ask.message_thread_id == SUPPORT_TOPIC_ID
