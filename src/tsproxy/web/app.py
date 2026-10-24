"""Веб-приложение FastAPI: вебхуки Telegram (сейчас), Remnawave, платёжки и `/health` (позже)."""

from fastapi import FastAPI

from tsproxy.web.telegram import TelegramWebhook


def create_web_app(telegram: TelegramWebhook | None) -> FastAPI:
    """`telegram` — приёмник вебхука Telegram; `None` в разработке (там polling)."""
    # Документация API наружу не нужна: меньше поверхность для сканеров
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    if telegram is not None:
        app.include_router(telegram.router())
    return app
