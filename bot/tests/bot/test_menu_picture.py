"""Картинка главного меню (bot-telegram-ui, задача 27).

Главное меню — фото с подписью (строка статуса и «Выберите, что нужно:») и кнопками.
Картинка — по статусу подписки (задача 04): на связи, доступ сохранён, нет доступа,
не подключено (нет прокси-аккаунта или панель не ответила). Файл загружается в Telegram
один раз, дальше бот шлёт его по `file_id`.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageReplyMarkup, SendMessage, SendPhoto
from aiogram.types import FSInputFile
from bot_updates import CLIENT_ID, NOW, callback, message, user
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.dates import client_time
from tsproxy.content.loader import Content
from tsproxy.db.models import Client
from tsproxy.domain.constants import BYTES_PER_GB
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable

PHOTO_CAPTION_MAX_LENGTH = 1024  # предел Telegram для подписи под фото
TRIAL_TRAFFIC = 7 * BYTES_PER_GB


def file_name(photo: object) -> str:
    """Имя файла картинки, которую бот загрузил в Telegram."""
    assert isinstance(photo, FSInputFile)
    return Path(photo.path).name


async def start(dispatcher: Dispatcher, bot: Bot) -> None:
    await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))


async def test_main_menu_is_photo_with_caption_and_buttons(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await start(dispatcher, bot)

    assert telegram.sent(SendMessage) == []
    [menu] = telegram.sent(SendPhoto)
    assert file_name(menu.photo) == "menu-new.png"  # нет прокси-аккаунта — «не подключено»
    assert menu.caption == content.texts.get("menu.title")
    assert menu.reply_markup is not None and menu.reply_markup.inline_keyboard


def test_caption_has_no_service_line_it_is_on_picture(content: Content) -> None:
    title = content.texts.get("menu.title")
    assert "прокси-сервис" not in title
    assert title.endswith("Выберите, что нужно:")


@pytest.mark.parametrize(
    ("expire_in", "grace_started", "trial", "picture"),
    [
        (timedelta(days=5), False, False, "menu-active.png"),
        (timedelta(days=5), False, True, "menu-active.png"),  # пробный — тоже на связи
        (timedelta(days=2), True, False, "menu-grace.png"),
        (-timedelta(days=10), False, False, "menu-expired.png"),
    ],
)
async def test_picture_follows_subscription_status(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    expire_in: timedelta,
    grace_started: bool,
    trial: bool,
    picture: str,
) -> None:
    async with session_factory() as session:
        session.add(
            Client(
                telegram_id=CLIENT_ID,
                first_seen_at=NOW,
                trial_granted_at=NOW if trial else None,
                grace_started_at=NOW - timedelta(hours=1) if grace_started else None,
            )
        )
        await session.commit()
    await remnawave.get_or_create_account(
        CLIENT_ID,
        expire_at=NOW + expire_in,
        device_limit=3,
        traffic_limit_bytes=TRIAL_TRAFFIC if trial else 0,
    )

    await start(dispatcher, bot)

    [menu] = telegram.sent(SendPhoto)
    assert file_name(menu.photo) == picture


async def test_panel_down_shows_not_connected_picture(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=NOW + timedelta(days=5), device_limit=3, traffic_limit_bytes=0
    )
    remnawave.fail_next(RemnawaveUnavailable("нет связи"))

    await start(dispatcher, bot)

    [menu] = telegram.sent(SendPhoto)
    assert file_name(menu.photo) == "menu-new.png"


async def test_picture_is_uploaded_once_then_sent_by_file_id(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await start(dispatcher, bot)
    await dispatcher.feed_update(
        bot, callback(user(CLIENT_ID), MenuCallback(screen=ScreenId.MAIN).pack())
    )

    first, second = telegram.sent(SendPhoto)
    assert isinstance(first.photo, FSInputFile)
    assert second.photo == telegram.photo_file_id(first.photo)


async def test_new_screen_removes_photo_menu(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """Живое меню и для фото: нажатие на нём — новый экран, фото удаляется."""
    await start(dispatcher, bot)
    menu_id = telegram.last_message_id

    await dispatcher.feed_update(
        bot,
        callback(user(CLIENT_ID), MenuCallback(screen=ScreenId.ABOUT).pack(), message_id=menu_id),
    )

    [deleted] = telegram.sent(DeleteMessage)
    assert deleted.message_id == menu_id


async def test_old_photo_menu_loses_buttons_when_not_deletable(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await start(dispatcher, bot)
    menu_id = telegram.last_message_id
    telegram.fail_next(
        DeleteMessage, TelegramBadRequest, "Bad Request: message can't be deleted for everyone"
    )

    await start(dispatcher, bot)

    [stripped] = telegram.sent(EditMessageReplyMarkup)
    assert stripped.message_id == menu_id
    assert stripped.reply_markup is None


def test_longest_caption_fits_photo_limit(content: Content) -> None:
    """Подпись — строка статуса и «Выберите, что нужно:»."""
    statuses = [
        content.texts.get(key, expire_at=client_time(NOW))
        for key in (
            "menu.status_trial",
            "menu.status_trial_traffic_used_up",
            "menu.status_active",
            "menu.status_grace",
            "menu.status_expired",
            "menu.status_suspended",
        )
    ]
    longest = max(statuses, key=len)
    assert len(f"{longest}\n\n{content.texts.get('menu.title')}") <= PHOTO_CAPTION_MAX_LENGTH


async def test_about_is_photo_with_caption_and_buttons(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    """«О сервисе» (задача 28) — фото с подписью, тем же способом, что главное меню."""
    await dispatcher.feed_update(
        bot, callback(user(CLIENT_ID), MenuCallback(screen=ScreenId.ABOUT).pack())
    )

    assert telegram.sent(SendMessage) == []
    [about] = telegram.sent(SendPhoto)
    assert file_name(about.photo) == "about.png"
    assert about.caption == (
        "Шифруем ваш трафик, скрываем IP-адрес и защищаем данные в публичных Wi-Fi сетях. "
        "До 3 устройств на одной подписке."
    )
    assert about.reply_markup is not None and about.reply_markup.inline_keyboard


async def test_rejected_file_id_is_forgotten_and_picture_uploaded_again(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    """Telegram отверг запомненный `file_id` (например, бот сменил токен) — картинка
    загружается заново, клиент получает меню без извинений."""
    await start(dispatcher, bot)
    telegram.fail_next(
        SendPhoto, TelegramBadRequest, "Bad Request: wrong file identifier/HTTP URL specified"
    )

    await start(dispatcher, bot)

    _, rejected, uploaded = telegram.sent(SendPhoto)
    assert isinstance(rejected.photo, str)
    assert file_name(uploaded.photo) == "menu-new.png"
    assert uploaded.caption == content.texts.get("menu.title")
    assert telegram.sent(SendMessage) == []


async def test_after_upload_again_picture_is_sent_by_new_file_id(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    await start(dispatcher, bot)
    telegram.fail_next(
        SendPhoto, TelegramBadRequest, "Bad Request: wrong file identifier/HTTP URL specified"
    )
    await start(dispatcher, bot)

    await start(dispatcher, bot)

    *_, uploaded, latest = telegram.sent(SendPhoto)
    assert isinstance(uploaded.photo, FSInputFile)
    assert latest.photo == telegram.photo_file_id(uploaded.photo)


async def test_other_bad_request_on_photo_is_not_retried(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """Отказ не из-за файла (подпись, кнопки) повторной загрузкой не лечится."""
    await start(dispatcher, bot)
    telegram.fail_next(SendPhoto, TelegramBadRequest, "Bad Request: message caption is too long")

    await start(dispatcher, bot)

    assert len(telegram.sent(SendPhoto)) == 2
