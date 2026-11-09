"""Веб-приложение FastAPI: вебхуки Telegram и Remnawave, `/health` (задача 22),
уведомления платёжки (задача 14), `/status` для плашки на сайте (задача сайта 13)."""

from fastapi import FastAPI

from tsproxy.web.health import HealthEndpoint
from tsproxy.web.payments import PaymentNotificationWebhook
from tsproxy.web.remnawave import RemnawaveWebhook
from tsproxy.web.status import StatusEndpoint
from tsproxy.web.telegram import TelegramWebhook


def create_web_app(
    telegram: TelegramWebhook | None,
    remnawave: RemnawaveWebhook | None = None,
    health: HealthEndpoint | None = None,
    payments: PaymentNotificationWebhook | None = None,
    status: StatusEndpoint | None = None,
) -> FastAPI:
    """`telegram` — приёмник вебхука Telegram; `None` в разработке (там polling).

    `remnawave` — приёмник вебхуков Remnawave; `None`, если секрет подписи не задан
    (тогда работает только сверка).

    `health` — `/health` для UptimeRobot; `None` — маршрута нет.

    `payments` — приёмник уведомлений платёжки (задача 14); `None`, если оплата выключена.

    `status` — `/status` для плашки на сайте (задача сайта 13); `None` — маршрута нет.
    """
    # Документация API наружу не нужна: меньше поверхность для сканеров
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    if telegram is not None:
        app.include_router(telegram.router())
    if remnawave is not None:
        app.include_router(remnawave.router())
    if health is not None:
        app.include_router(health.router())
    if payments is not None:
        app.include_router(payments.router())
    if status is not None:
        app.include_router(status.router())
    return app
