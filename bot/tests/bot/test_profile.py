"""«👤 Мой профиль» и «📱 Мои устройства»: только свои данные, без технических кодов."""

from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot_updates import (
    ADMIN_CHAT_ID,
    ALERTS_THREAD_ID,
    CLIENT_ID,
    MENU_MESSAGE_ID,
    NOW,
    OTHER_CLIENT_ID,
    callback,
    message,
    user,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import DeviceCallback, MenuCallback, ScreenId
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.profile_screens import ProfileScreens
from tsproxy.content.loader import REQUIRED_TEXTS, Content, Texts
from tsproxy.db.models import Client
from tsproxy.domain.constants import BYTES_PER_GB
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.profile import ProfileStatus, TimeLeft
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import Device, ProxyAccount, RemnawaveUnavailable
from tsproxy.services.fakes import FakeAlertSender
from tsproxy.services.profile import DeviceList, DeviceSlots, Profile, TrafficUsage, device_ref

PROFILE = MenuCallback(screen=ScreenId.PROFILE).pack()
DEVICES = MenuCallback(screen=ScreenId.DEVICES).pack()
EXPIRE_AT = NOW + timedelta(days=5, hours=3)
TRIAL_TRAFFIC = 7 * BYTES_PER_GB
HWID = "f1e2d3c4b5a69788"


async def create_account(
    remnawave: FakeRemnawave, telegram_id: int = CLIENT_ID, *, traffic_limit_bytes: int = 0
) -> ProxyAccount:
    return await remnawave.get_or_create_account(
        telegram_id, expire_at=EXPIRE_AT, device_limit=3, traffic_limit_bytes=traffic_limit_bytes
    )


async def add_client(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_id: int = CLIENT_ID,
    **fields: object,
) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=telegram_id, first_seen_at=NOW, **fields))
        await session.commit()


def device(hwid: str = HWID, *, model: str | None = "iPhone 15") -> Device:
    return Device(hwid=hwid, platform="iOS", os_version="18.1", device_model=model, created_at=NOW)


def buttons(markup: object) -> list[InlineKeyboardButton]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b for row in markup.inline_keyboard for b in row]


def delete_button_data(markup: object) -> list[str]:
    return [
        b.callback_data
        for b in buttons(markup)
        if b.callback_data is not None and b.callback_data.startswith("device:")
    ]


# --- Профиль ---


async def test_profile_shows_status_term_and_devices(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    account = await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device())

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), PROFILE))

    [edited] = telegram.sent(EditMessageText)
    assert (edited.chat_id, edited.message_id) == (CLIENT_ID, MENU_MESSAGE_ID)
    status = content.texts.get(
        "profile.status_active", expire_at=format_moscow(EXPIRE_AT), left="5 дней 3 часа"
    )
    assert status in edited.text
    assert content.texts.get("profile.devices", used=1, limit=3) in edited.text
    assert "ГБ" not in edited.text  # трафик — только в пробном
    # Без имени аккаунта в панели, HWID и ссылки подписки
    for code in (account.username, HWID, account.subscription_url):
        assert code not in edited.text
    assert content.texts.get("buttons.devices") in [b.text for b in buttons(edited.reply_markup)]


async def test_trial_profile_shows_traffic(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    await add_client(session_factory, trial_granted_at=NOW)
    await create_account(remnawave, traffic_limit_bytes=TRIAL_TRAFFIC)
    remnawave.set_traffic_used(CLIENT_ID, BYTES_PER_GB * 23 // 10)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), PROFILE))

    [edited] = telegram.sent(EditMessageText)
    assert content.texts.get("profile.traffic", used="2,3", limit="7") in edited.text
    assert "пробный период" in edited.text


async def test_profile_command_sends_new_message(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await create_account(remnawave)

    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/profile"))

    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == CLIENT_ID
    assert content.texts.get("profile.title") in sent.text


async def test_profile_without_account_offers_trial(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), PROFILE))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("profile.no_account")
    assert content.texts.get("buttons.trial") in [b.text for b in buttons(edited.reply_markup)]


async def test_profile_of_another_client_is_never_shown(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    """Профиль — по `from_user.id` нажавшего, кнопка под чужим сообщением не раскрывает чужое."""
    await create_account(remnawave, CLIENT_ID)
    remnawave.simulate_device(CLIENT_ID, device(model="Секретный телефон"))

    await dispatcher.feed_update(bot, callback(user(OTHER_CLIENT_ID), PROFILE))
    await dispatcher.feed_update(bot, callback(user(OTHER_CLIENT_ID), DEVICES))

    for request in telegram.requests:
        assert "Секретный телефон" not in request.model_dump_json(fallback=repr)
        assert format_moscow(EXPIRE_AT) not in request.model_dump_json(fallback=repr)


async def test_panel_unavailable_apologizes_and_alerts(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), PROFILE))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("connect.unavailable")
    [alert] = alert_sender.sent
    assert (alert.chat_id, alert.thread_id) == (ADMIN_CHAT_ID, ALERTS_THREAD_ID)


# --- Устройства ---


async def test_devices_list_with_delete_buttons(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device("a"))
    remnawave.simulate_device(CLIENT_ID, device("b", model=None))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), DEVICES))

    [edited] = telegram.sent(EditMessageText)
    assert content.texts.get("devices.title", used=2, limit=3) in edited.text
    assert "iPhone 15" in edited.text
    assert content.texts.get("devices.unknown_model") in edited.text
    assert format_moscow(NOW) in edited.text
    assert delete_button_data(edited.reply_markup) == [
        DeviceCallback(ref=device_ref("a")).pack(),
        DeviceCallback(ref=device_ref("b")).pack(),
    ]


async def test_delete_own_device(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device("a"))
    remnawave.simulate_device(CLIENT_ID, device("b"))

    data = DeviceCallback(ref=device_ref("a")).pack()
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    assert [d.hwid for d in await remnawave.list_devices(CLIENT_ID)] == ["b"]
    [edited] = telegram.sent(EditMessageText)
    assert content.texts.get("devices.deleted") in edited.text
    assert delete_button_data(edited.reply_markup) == [DeviceCallback(ref=device_ref("b")).pack()]


async def test_forged_button_cannot_delete_device_of_another_client(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    """Подменённая кнопка с устройством чужого клиента: удаления нет (аудит §4.3)."""
    await create_account(remnawave, CLIENT_ID)
    await create_account(remnawave, OTHER_CLIENT_ID)
    remnawave.simulate_device(CLIENT_ID, device("victim"))
    writes_before = list(remnawave.writes)

    forged = DeviceCallback(ref=device_ref("victim")).pack()
    await dispatcher.feed_update(bot, callback(user(OTHER_CLIENT_ID), forged))

    assert [d.hwid for d in await remnawave.list_devices(CLIENT_ID)] == ["victim"]
    assert remnawave.writes == writes_before
    [edited] = telegram.sent(EditMessageText)
    assert content.texts.get("devices.not_found") in edited.text


async def test_malformed_delete_button_is_ignored(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    # Сырой HWID вместо ссылки не проходит проверку данных кнопки — до панели не доходит
    await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device("a"))
    writes_before = list(remnawave.writes)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), "device:a"))

    assert remnawave.writes == writes_before
    assert telegram.sent(EditMessageText) == []


async def test_start_from_subscription_page_opens_devices(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    """Кнопка «Устройства» на странице подписки: `t.me/<бот>?start=devices`."""
    await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device("a"))

    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start devices"))

    [sent] = telegram.sent(SendMessage)
    assert content.texts.get("devices.title", used=1, limit=3) in sent.text
    assert delete_button_data(sent.reply_markup) == [DeviceCallback(ref=device_ref("a")).pack()]


async def test_devices_without_account(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), DEVICES))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("profile.no_account")


async def test_no_devices_yet(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await create_account(remnawave)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), DEVICES))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("devices.empty")


def test_device_name_from_app_is_escaped(content: Content) -> None:
    # Модель устройства присылает приложение клиента — разметку из неё не исполняем
    devices = DeviceList((device(model="<b>x</b>"),), limit=3)
    screen = ProfileScreens(content.texts).devices(devices)
    assert "&lt;b&gt;x&lt;/b&gt;" in screen.text


# --- Все экраны собираются из обязательных текстов ---


def test_all_profile_screens_use_only_required_texts(content: Content) -> None:
    """Текст не из REQUIRED_TEXTS уронил бы бота у клиента, а не при запуске."""
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    screens = ProfileScreens(texts)
    traffic = TrafficUsage(used_bytes=1, limit_bytes=TRIAL_TRAFFIC)
    for status in ProfileStatus:
        if status is ProfileStatus.NO_SUBSCRIPTION:
            continue  # без прокси-аккаунта профиля нет — экран no_account
        for left in (TimeLeft(1, 0), TimeLeft(0, 2), TimeLeft(0, 0), TimeLeft(5, 21)):
            for limit in (3, None):
                profile = Profile(status, EXPIRE_AT, left, DeviceSlots(1, limit), traffic)
                screens.profile(profile)
    screens.no_account(trial_available=True)
    with_system = device()
    without_system = Device(
        hwid="x", platform=None, os_version=None, device_model=None, created_at=NOW
    )
    for limit in (3, None):
        for notice in (None, "devices.deleted", "devices.not_found"):
            screens.devices(DeviceList((with_system, without_system), limit), notice_key=notice)
            screens.devices(DeviceList((), limit), notice_key=notice)
