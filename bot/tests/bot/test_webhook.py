"""Вебхук Telegram (аудит R01): без верного секрета апдейт не доходит до бота."""

from collections.abc import AsyncIterator

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendPhoto
from bot_updates import BOT_TOKEN, CLIENT_ID, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.db.models import Client
from tsproxy.web.app import create_web_app
from tsproxy.web.telegram import (
    SECRET_HEADER,
    TELEGRAM_WEBHOOK_PATH,
    TelegramWebhook,
    secret_matches,
    webhook_url,
)

SECRET = "s3cret-" + "x" * 32


@pytest.fixture
async def http(bot: Bot, dispatcher: Dispatcher) -> AsyncIterator[httpx.AsyncClient]:
    app = create_web_app(TelegramWebhook(bot, dispatcher, SECRET))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
        yield client


def start_update() -> dict[str, object]:
    return message(user(CLIENT_ID), "/start").model_dump(mode="json", exclude_none=True)


async def client_count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return len((await session.scalars(select(Client))).all())


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {SECRET_HEADER: ""},
        {SECRET_HEADER: "wrong"},
        {SECRET_HEADER: SECRET[:-1]},
        {SECRET_HEADER: SECRET + "x"},
    ],
    ids=["no-header", "empty", "wrong", "prefix", "longer"],
)
async def test_update_without_valid_secret_is_rejected(
    http: httpx.AsyncClient,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    headers: dict[str, str],
) -> None:
    response = await http.post(TELEGRAM_WEBHOOK_PATH, json=start_update(), headers=headers)

    assert response.status_code == httpx.codes.FORBIDDEN
    assert telegram.requests == []  # бот ничего не сделал
    assert await client_count(session_factory) == 0


async def test_update_with_secret_is_processed(
    http: httpx.AsyncClient,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await http.post(
        TELEGRAM_WEBHOOK_PATH, json=start_update(), headers={SECRET_HEADER: SECRET}
    )

    assert response.status_code == httpx.codes.OK
    assert [m.chat_id for m in telegram.sent(SendPhoto)] == [CLIENT_ID]  # главное меню
    assert await client_count(session_factory) == 1


async def test_garbage_body_with_secret_is_bad_request(
    http: httpx.AsyncClient, telegram: FakeTelegramSession
) -> None:
    response = await http.post(
        TELEGRAM_WEBHOOK_PATH, content=b"not json", headers={SECRET_HEADER: SECRET}
    )

    assert response.status_code == httpx.codes.BAD_REQUEST
    assert telegram.requests == []


async def test_no_webhook_route_in_dev() -> None:
    app = create_web_app(None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
        response = await client.post(TELEGRAM_WEBHOOK_PATH, json=start_update())
    assert response.status_code == httpx.codes.NOT_FOUND


def test_webhook_url_has_no_bot_token() -> None:
    url = webhook_url("https://bot.ts-proxy.com/")
    assert url == "https://bot.ts-proxy.com/telegram/webhook"
    assert BOT_TOKEN not in url
    assert BOT_TOKEN.split(":", 1)[1] not in url


def test_secret_comparison() -> None:
    assert secret_matches(SECRET, SECRET) is True
    assert secret_matches(None, SECRET) is False
    assert secret_matches("ключ", SECRET) is False  # не-ASCII не роняет сравнение
