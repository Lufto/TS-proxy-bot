"""Поставить профиль бота: аватар, описание и короткое описание (задача 29 bot-telegram-ui).

Бот — тот, чей `BOT_TOKEN` в `.env`. Тексты — `content/client/bot_profile.yaml`,
аватар — `content/images/avatar.jpg`. Запускать можно сколько угодно раз: совпадающее
не меняется, аватар ставится, только если его нет (подробно — `tsproxy.bot.bot_profile`).

    uv run python scripts/set_bot_profile.py                   # из папки bot/
    uv run python scripts/set_bot_profile.py --replace-avatar  # заменить уже стоящий аватар

Картинку описания в пустом чате ставит только владелец в @BotFather —
шаги в `docs/areas/server.md`, «Профиль бота».

Код выхода 0 — профиль в порядке; 1 — ошибка в `.env`, текстах, нет аватара (тогда
Telegram не трогаем) или ответ Telegram с ошибкой.
"""

import argparse
import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.token import TokenValidationError
from pydantic import ValidationError

from tsproxy.bot.bot_profile import BotProfile, ProfileUpdate, apply_bot_profile, bot_profile
from tsproxy.config import describe_settings_error, load_settings
from tsproxy.content.loader import ContentError, load_content
from tsproxy.logging_setup import setup_logging

logger = logging.getLogger("set_bot_profile")
# Уровень журнала, пока `.env` не прочитан (дальше — LOG_LEVEL из `.env`)
DEFAULT_LOG_LEVEL = "INFO"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Поставить аватар и описания бота")
    parser.add_argument(
        "--replace-avatar",
        action="store_true",
        help="заменить аватар, даже если он уже стоит (добавит фото в историю аватаров)",
    )
    return parser.parse_args()


async def _apply(token: str, profile: BotProfile, *, replace_avatar: bool) -> ProfileUpdate:
    async with Bot(token) as bot:
        me = await bot.get_me()
        logger.info("Бот @%s", me.username)
        return await apply_bot_profile(bot, profile, replace_avatar=replace_avatar)


def _report(update: ProfileUpdate) -> None:
    if update.changed:
        logger.info("Поставлено: %s", ", ".join(update.changed))
    else:
        logger.info("Всё уже стоит, ничего не меняли")
    if update.avatar_kept:
        logger.info("Аватар у бота уже есть — не трогали. Заменить: --replace-avatar")


def main() -> int:
    args = _parse_args()
    # до чтения `.env` секретов ещё нет — журнал без маскировки, в нём только имена переменных
    setup_logging(DEFAULT_LOG_LEVEL, ())
    try:
        settings = load_settings()
    except ValidationError as error:
        logger.error("Ошибка в .env, проверьте переменные: %s", describe_settings_error(error))
        return 1
    setup_logging(settings.log_level, settings.secret_values())
    try:
        content = load_content(settings.content_dir, settings.config_dir)
        profile = bot_profile(content.texts, settings.content_dir)
    except ContentError as error:
        logger.error("Тексты или аватар с ошибкой: %s", error)
        return 1
    token = settings.bot_token.get_secret_value()
    try:
        update = asyncio.run(_apply(token, profile, replace_avatar=args.replace_avatar))
    except (TokenValidationError, TelegramAPIError) as error:
        logger.error("Telegram не принял профиль: %s: %s", type(error).__name__, error)
        return 1
    _report(update)
    return 0


if __name__ == "__main__":
    sys.exit(main())
