"""Веб-приложение FastAPI: вебхуки Telegram (сейчас), Remnawave, платёжки и `/health` (позже)."""

from fastapi import FastAPI

from tsproxy.web.remnawave import RemnawaveWebhook
from tsproxy.web.telegram import TelegramWebhook


def create_web_app(
    telegram: TelegramWebhook | None, remnawave: RemnawaveWebhook | None = None
) -> FastAPI:
    """`telegram` — приёмник вебхука Telegram; `None` в разработке (там polling).

    `remnawave` — приёмник вебхуков Remnawave; `None`, если секрет подписи не задан
    (тогда работает только сверка).
    """
    # Документация API наружу не нужна: меньше поверхность для сканеров
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    if telegram is not None:
        app.include_router(telegram.router())
    if remnawave is not None:
        app.include_router(remnawave.router())
    return app
