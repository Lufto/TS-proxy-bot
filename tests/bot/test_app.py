"""Сборка приложения и подключение к Telegram — без сети (подделка Telegram)."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from aiogram.methods import DeleteWebhook, SetMyCommands, SetWebhook
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, BOT_TOKEN
from pydantic import SecretStr, ValidationError

from tsproxy.app import (
    Application,
    PeriodicJob,
    PeriodicJobs,
    build_application,
    close_application,
    connect_telegram,
)
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.config import PaymentsMode, RunMode, Settings
from tsproxy.content.loader import Content
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.web.telegram import TELEGRAM_WEBHOOK_PATH

WEBHOOK_SECRET = "A" * 40
PUBLIC_BASE_URL = "https://bot.ts-proxy.com"


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": BOT_TOKEN,
        "remnawave_token": "rw-token",
        "admin_ids": str(ADMIN_ID),
        "admin_chat_id": ADMIN_CHAT_ID,
        "alerts_thread_id": 2,
        "backups_thread_id": 3,
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def prod_settings(tmp_path: Path) -> Settings:
    return make_settings(
        tmp_path,
        run_mode=RunMode.PROD,
        payments_mode=PaymentsMode.REAL,
        public_base_url=PUBLIC_BASE_URL,
        telegram_webhook_secret=WEBHOOK_SECRET,
    )


@pytest.fixture
def telegram_session() -> Iterator[FakeTelegramSession]:
    yield FakeTelegramSession()


async def webhook_status(app: Application) -> int:
    """Код ответа вебхука на запрос без секрета: 404 — маршрута нет, 403 — есть и защищён."""
    transport = httpx.ASGITransport(app=app.web)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
        response = await client.post(TELEGRAM_WEBHOOK_PATH, json={})
    return response.status_code


async def test_dev_removes_webhook_for_polling(
    tmp_path: Path, content: Content, telegram_session: FakeTelegramSession
) -> None:
    app = build_application(make_settings(tmp_path), content, telegram_session=telegram_session)
    try:
        await connect_telegram(app)
    finally:
        await close_application(app)

    [delete] = telegram_session.sent(DeleteWebhook)
    assert delete.drop_pending_updates is False
    assert telegram_session.sent(SetWebhook) == []
    assert len(telegram_session.sent(SetMyCommands)) == 1
    assert await webhook_status(app) == httpx.codes.NOT_FOUND
    assert telegram_session.closed


async def test_prod_sets_webhook_with_secret(
    tmp_path: Path, content: Content, telegram_session: FakeTelegramSession
) -> None:
    app = build_application(
        prod_settings(tmp_path),
        content,
        telegram_session=telegram_session,
        payments=FakePaymentProvider(lambda: datetime.now(UTC)),
    )
    try:
        await connect_telegram(app)
    finally:
        await close_application(app)

    [webhook] = telegram_session.sent(SetWebhook)
    assert webhook.url == PUBLIC_BASE_URL + TELEGRAM_WEBHOOK_PATH
    assert BOT_TOKEN.split(":", 1)[1] not in webhook.url
    assert webhook.secret_token == WEBHOOK_SECRET
    assert webhook.drop_pending_updates is False
    assert {"message", "callback_query", "my_chat_member"} <= set(webhook.allowed_updates or [])
    assert telegram_session.sent(DeleteWebhook) == []
    assert await webhook_status(app) == httpx.codes.FORBIDDEN


@pytest.mark.parametrize(
    "overrides",
    [
        {"telegram_webhook_secret": None},
        {"telegram_webhook_secret": "short"},
        {"telegram_webhook_secret": "A" * 31 + "!"},
        {"public_base_url": None},
        {"public_base_url": "http://bot.ts-proxy.com"},
    ],
)
def test_prod_requires_https_url_and_strong_secret(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    values: dict[str, object] = {
        "run_mode": RunMode.PROD,
        "payments_mode": PaymentsMode.REAL,
        "public_base_url": PUBLIC_BASE_URL,
        "telegram_webhook_secret": WEBHOOK_SECRET,
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        make_settings(tmp_path, **values)


def test_dev_does_not_need_webhook_settings(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, public_base_url="", telegram_webhook_secret="")
    assert settings.public_base_url is None
    assert settings.telegram_webhook_secret is None


def test_webhook_secret_is_masked_in_logs(tmp_path: Path) -> None:
    settings = prod_settings(tmp_path)
    assert isinstance(settings.telegram_webhook_secret, SecretStr)
    assert WEBHOOK_SECRET in settings.secret_values()
    assert WEBHOOK_SECRET not in repr(settings)


async def test_periodic_job_survives_errors() -> None:
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("первый раз падает")

    jobs = PeriodicJobs([PeriodicJob("flaky", timedelta(milliseconds=1), flaky)])
    jobs.start()
    for _ in range(100):
        if calls >= 3:
            break
        await asyncio.sleep(0.005)
    await jobs.stop()
    assert calls >= 3


def test_real_payments_without_provider_refuse_to_start(tmp_path: Path, content: Content) -> None:
    """Настоящей платёжки ещё нет (задача 15): лучше не стартовать, чем принять заказ в никуда."""
    with pytest.raises(ValueError, match="задача 15"):
        build_application(prod_settings(tmp_path), content, telegram_session=FakeTelegramSession())
