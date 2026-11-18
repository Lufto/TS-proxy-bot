"""«🔄 Перевыпустить ссылку» (задача 31): только своя ссылка, предупреждение, раз в сутки."""

import asyncio
import logging
from datetime import timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot_updates import ADMIN_CHAT_ID, ALERTS_THREAD_ID, CLIENT_ID, NOW, OTHER_CLIENT_ID, user
from bot_updates import callback as press

from tsproxy.bot.callbacks import MenuCallback, RevokeLinkCallback, ScreenId
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.profile_screens import ProfileScreens
from tsproxy.content.dates import client_time
from tsproxy.content.loader import REQUIRED_TEXTS, Content, Texts
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.fakes import FakeAlertSender, FakeClock

PROFILE = MenuCallback(screen=ScreenId.PROFILE).pack()
DEVICES = MenuCallback(screen=ScreenId.DEVICES).pack()
ASK = MenuCallback(screen=ScreenId.REVOKE_LINK).pack()
YES = RevokeLinkCallback().pack()


async def create_account(remnawave: FakeRemnawave, telegram_id: int = CLIENT_ID) -> str:
    """Платный аккаунт; возвращает нынешнюю ссылку подписки."""
    account = await remnawave.get_or_create_account(
        telegram_id, expire_at=NOW + timedelta(days=30), device_limit=3, traffic_limit_bytes=0
    )
    return account.subscription_url


def buttons(markup: object) -> list[InlineKeyboardButton]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b for row in markup.inline_keyboard for b in row]


def callback_data(markup: object) -> list[str | None]:
    return [b.callback_data for b in buttons(markup)]


def revoke_writes(remnawave: FakeRemnawave) -> int:
    return sum(1 for operation, _ in remnawave.writes if operation == "revoke")


def last_screen(telegram: FakeTelegramSession) -> EditMessageText:
    return telegram.sent(EditMessageText)[-1]


@pytest.mark.parametrize("screen", [PROFILE, DEVICES])
async def test_profile_and_devices_offer_revoke(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    screen: str,
) -> None:
    await create_account(remnawave)

    await dispatcher.feed_update(bot, press(user(CLIENT_ID), screen))

    assert ASK in callback_data(last_screen(telegram).reply_markup)


async def test_revoke_asks_for_confirmation_first(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await create_account(remnawave)

    await dispatcher.feed_update(bot, press(user(CLIENT_ID), ASK))

    edited = last_screen(telegram)
    assert edited.text == content.texts.get("revoke_link.ask")
    assert callback_data(edited.reply_markup) == [YES, PROFILE]
    assert revoke_writes(remnawave) == 0


async def test_yes_revokes_and_shows_new_link(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    old_url = await create_account(remnawave)

    await dispatcher.feed_update(bot, press(user(CLIENT_ID), YES))

    new_url = (await remnawave.get_account(CLIENT_ID)).subscription_url
    assert new_url != old_url
    edited = last_screen(telegram)
    assert edited.text == content.texts.get("revoke_link.done")
    links = buttons(edited.reply_markup)
    assert [b.url for b in links if b.url] == [new_url]
    assert [b.copy_text.text for b in links if b.copy_text] == [new_url]
    assert old_url not in edited.model_dump_json(fallback=repr)


async def test_second_revoke_within_a_day_shows_when_possible(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    clock: FakeClock,
    content: Content,
) -> None:
    await create_account(remnawave)
    await dispatcher.feed_update(bot, press(user(CLIENT_ID), YES))
    clock.advance(timedelta(hours=5))

    # И сама кнопка, и подделанное «Да» без предупреждения — отказ с датой
    for data in (ASK, YES):
        await dispatcher.feed_update(bot, press(user(CLIENT_ID), data))
        edited = last_screen(telegram)
        when = client_time(NOW + timedelta(days=1))
        assert edited.text == content.texts.get("revoke_link.too_soon", available_at=when)
        assert MenuCallback(screen=ScreenId.CONNECT).pack() in callback_data(edited.reply_markup)
    assert revoke_writes(remnawave) == 1


async def test_revoke_allowed_again_next_day(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    clock: FakeClock,
    content: Content,
) -> None:
    await create_account(remnawave)
    await dispatcher.feed_update(bot, press(user(CLIENT_ID), YES))
    clock.advance(timedelta(days=1))

    await dispatcher.feed_update(bot, press(user(CLIENT_ID), ASK))
    assert last_screen(telegram).text == content.texts.get("revoke_link.ask")
    await dispatcher.feed_update(bot, press(user(CLIENT_ID), YES))

    assert last_screen(telegram).text == content.texts.get("revoke_link.done")
    assert revoke_writes(remnawave) == 2


async def test_double_yes_revokes_once(
    dispatcher: Dispatcher, bot: Bot, remnawave: FakeRemnawave
) -> None:
    await create_account(remnawave)

    await asyncio.gather(
        dispatcher.feed_update(bot, press(user(CLIENT_ID), YES)),
        dispatcher.feed_update(bot, press(user(CLIENT_ID), YES)),
    )

    assert revoke_writes(remnawave) == 1


async def test_only_own_link_is_revoked(
    dispatcher: Dispatcher, bot: Bot, remnawave: FakeRemnawave
) -> None:
    """Кнопка не несёт, чью ссылку перевыпустить: только нажавшего."""
    victim_url = await create_account(remnawave, CLIENT_ID)
    await create_account(remnawave, OTHER_CLIENT_ID)

    await dispatcher.feed_update(bot, press(user(OTHER_CLIENT_ID), YES))

    assert (await remnawave.get_account(CLIENT_ID)).subscription_url == victim_url
    assert revoke_writes(remnawave) == 1


async def test_without_account_shows_no_subscription(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, press(user(CLIENT_ID), YES))

    assert last_screen(telegram).text == content.texts.get("profile.no_account")
    assert remnawave.writes == []


async def test_panel_unavailable_apologizes_and_alerts(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    await create_account(remnawave)
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    await dispatcher.feed_update(bot, press(user(CLIENT_ID), YES))

    assert last_screen(telegram).text == content.texts.get("connect.unavailable")
    [alert] = alert_sender.sent
    assert (alert.chat_id, alert.thread_id) == (ADMIN_CHAT_ID, ALERTS_THREAD_ID)


async def test_new_link_is_not_logged(
    dispatcher: Dispatcher, bot: Bot, remnawave: FakeRemnawave, caplog: pytest.LogCaptureFixture
) -> None:
    old_url = await create_account(remnawave)
    caplog.set_level(logging.DEBUG)

    await dispatcher.feed_update(bot, press(user(CLIENT_ID), YES))

    new_url = (await remnawave.get_account(CLIENT_ID)).subscription_url
    assert new_url not in caplog.text
    assert old_url not in caplog.text


def test_revoke_screens_use_only_required_texts(content: Content) -> None:
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    screens = ProfileScreens(texts)
    screens.revoke_ask()
    screens.revoke_too_soon(NOW)
