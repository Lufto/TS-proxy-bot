"""Сборка приложения с оплатой выключенной (`PAYMENTS_MODE=off`, задача 29) — без сети."""

import logging
from pathlib import Path

import httpx
import pytest
from aiogram.methods import EditMessageText, SendMessage
from bot_updates import ADMIN_CHAT_ID, CLIENT_ID, callback, user
from sqlalchemy import select
from test_app import make_settings, prod_settings

from tsproxy.app import build_application, check_payments_mode, close_application
from tsproxy.bot.callbacks import TariffCallback
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.config import PaymentsMode
from tsproxy.content.loader import Content
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Base, Order
from tsproxy.web.payments import PAYMENT_NOTIFICATION_PATH


async def test_prod_with_payments_off_starts_and_sells_nothing(
    tmp_path: Path, content: Content
) -> None:
    """«Готово, когда» задачи 29: `prod` + `off` собирается, «Купить» не создаёт заказ."""
    telegram = FakeTelegramSession()
    settings = prod_settings(tmp_path).model_copy(update={"payments_mode": PaymentsMode.OFF})
    app = build_application(settings, content, telegram_session=telegram)
    try:
        async with app.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        buy = TariffCallback(tariff_id="m1").pack()

        await app.dispatcher.feed_update(app.bot, callback(user(CLIENT_ID), buy))

        async with create_session_factory(app.engine)() as session:
            assert list(await session.scalars(select(Order))) == []
    finally:
        await close_application(app)
    edited = telegram.sent(EditMessageText)[-1]
    assert edited.text.startswith(content.texts.get("tariffs.off_title"))
    # уже оплаченные заказы (если оплата была включена раньше) доделываются и без платёжки
    assert "payment_grants" in [job.name for job in app.jobs._jobs]
    # уведомлений платёжки не принимаем: перепроверить их не у кого (задача 14)
    transport = httpx.ASGITransport(app=app.web)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as http:
        response = await http.post(PAYMENT_NOTIFICATION_PATH, content=b"{}")
    assert response.status_code == httpx.codes.NOT_FOUND


async def test_payments_off_is_announced_at_start(
    tmp_path: Path, content: Content, caplog: pytest.LogCaptureFixture
) -> None:
    telegram = FakeTelegramSession()
    settings = prod_settings(tmp_path).model_copy(update={"payments_mode": PaymentsMode.OFF})
    app = build_application(settings, content, telegram_session=telegram)
    try:
        with caplog.at_level(logging.WARNING):
            await check_payments_mode(app)
    finally:
        await close_application(app)

    [alert] = telegram.sent(SendMessage)
    assert alert.chat_id == ADMIN_CHAT_ID
    assert content.admin_texts.get("alerts.payments_off") in alert.text
    assert "PAYMENTS_MODE=off" in caplog.text


async def test_no_announcement_while_payments_on(tmp_path: Path, content: Content) -> None:
    telegram = FakeTelegramSession()
    app = build_application(make_settings(tmp_path), content, telegram_session=telegram)
    try:
        await check_payments_mode(app)
    finally:
        await close_application(app)

    assert telegram.sent(SendMessage) == []


async def test_payment_checks_run_quietly_while_payments_off(
    tmp_path: Path, content: Content
) -> None:
    """Задача 14: фоновые проверки оплат и сверка без платёжки ничего не делают и не падают."""
    telegram = FakeTelegramSession()
    settings = prod_settings(tmp_path).model_copy(update={"payments_mode": PaymentsMode.OFF})
    app = build_application(settings, content, telegram_session=telegram)
    try:
        jobs = {job.name: job for job in app.jobs._jobs}
        for name in ("payment_checks_fresh", "payment_checks_stale", "payments_reconcile"):
            await jobs[name].run()
    finally:
        await close_application(app)

    assert telegram.sent(SendMessage) == []
