"""Ошибки в обработчиках: в лог с трассировкой, тревога администраторам, извинение клиенту."""

import logging

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent, User

from tsproxy.bot.context import BotContext

logger = logging.getLogger(__name__)

# Одинаковые ошибки склеиваются в одну тревогу по типу исключения
BOT_ERROR_ALERT_PREFIX = "bot_error:"


def _private_sender(event: ErrorEvent) -> User | None:
    """Кому извиниться: только клиенту в личном чате. В группах молчим."""
    update = event.update
    if update.callback_query is not None:
        message = update.callback_query.message
        if message is not None and message.chat.type == ChatType.PRIVATE:
            return update.callback_query.from_user
    if update.message is not None and update.message.chat.type == ChatType.PRIVATE:
        return update.message.from_user
    return None


async def on_error(event: ErrorEvent, ctx: BotContext) -> bool:
    """Ошибка не потерялась: она в логе и в «🚨 Тревогах». Клиенту — «попробуйте позже»."""
    error = event.exception
    error_type = type(error).__name__
    logger.error(
        "Ошибка при обработке апдейта %s: %s",
        event.update.update_id,
        error_type,
        exc_info=error,
    )
    await ctx.alerter.raise_alert(
        BOT_ERROR_ALERT_PREFIX + error_type,
        ctx.admin_texts.get("alerts.bot_error", error=error_type),
    )
    user = _private_sender(event)
    if user is None:
        return True
    try:
        await ctx.delivery.send(user.id, ctx.screens.error())
    # Извинение не должно порождать новую ошибку в обработчике ошибок; сбой — в лог
    except TelegramAPIError:
        logger.exception("Не удалось извиниться перед клиентом %s", user.id)
    return True
