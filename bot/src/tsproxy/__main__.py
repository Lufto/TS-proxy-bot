"""Точка запуска: `uv run tsproxy`.

Проверяет настройки и тексты, собирает приложение (`tsproxy.app`) и запускает бота.
Неверные настройки, тексты, токен или версия панели — понятная ошибка и код выхода 1.
"""

import asyncio
import logging
import sys

from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.utils.token import TokenValidationError
from pydantic import ValidationError

from tsproxy.app import build_application, run_application
from tsproxy.config import load_settings
from tsproxy.content.loader import ContentError, load_content
from tsproxy.integrations.remnawave_types import RemnawaveAuthError, UnexpectedPanelVersion
from tsproxy.logging_setup import setup_logging

logger = logging.getLogger(__name__)

# Ошибки запуска, при которых работать нельзя: исправить настройки или панель и перезапустить
FATAL_STARTUP_ERRORS = (
    TokenValidationError,  # BOT_TOKEN не похож на токен
    TelegramUnauthorizedError,  # Telegram не принял BOT_TOKEN
    RemnawaveAuthError,  # панель не приняла REMNAWAVE_TOKEN
    UnexpectedPanelVersion,  # бот написан под другую версию панели
)


def _describe(error: ValidationError) -> str:
    """Какие переменные `.env` с ошибкой — без значений (там могут быть секреты)."""
    parts = []
    for item in error.errors():
        location = item["loc"]
        if location:
            parts.append(str(location[0]).upper())
        else:
            # проверка нескольких полей сразу (например, вебхук в режиме prod)
            parts.append(item["msg"].removeprefix("Value error, "))
    return ", ".join(parts)


def main() -> int:
    try:
        settings = load_settings()
    except ValidationError as error:
        print(f"Ошибка в .env, проверьте переменные: {_describe(error)}", file=sys.stderr)
        return 1
    setup_logging(settings.log_level, settings.secret_values())
    try:
        content = load_content(settings.content_dir, settings.config_dir)
    except ContentError as error:
        logger.error("Тексты или тарифы с ошибкой: %s", error)
        return 1
    logger.info(
        "Настройки и тексты в порядке: режим %s, тарифов %d",
        settings.run_mode,
        len(content.tariffs),
    )
    try:
        asyncio.run(run_application(build_application(settings, content)))
    except FATAL_STARTUP_ERRORS as error:
        logger.error("Бот не запущен: %s: %s", type(error).__name__, error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
