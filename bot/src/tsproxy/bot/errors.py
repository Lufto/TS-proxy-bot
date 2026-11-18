"""Ошибки в обработчиках: в лог с трассировкой, тревога администраторам, извинение клиенту.

В тревоге — что обрабатывалось: вид нажатой кнопки или команда, личный чат или группа.
Текст сообщений и данные кнопок целиком не показываем: там могут быть личные данные.
"""

import logging

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Chat, ErrorEvent, Update, User

from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import KEPT_TEXT_MARK
from tsproxy.content.loader import Texts
from tsproxy.services.bot_errors import alert_bot_error

logger = logging.getLogger(__name__)

COMMAND_START = "/"
CALLBACK_SEPARATOR = ":"  # данные кнопки aiogram: «вид:поле:поле»
BOT_NAME_SEPARATOR = "@"  # /client@tsproxy_bot


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
    logger.error(
        "Ошибка при обработке апдейта %s: %s",
        event.update.update_id,
        type(error).__name__,
        exc_info=error,
    )
    action = _handled(event.update, ctx.admin_texts)
    await alert_bot_error(ctx.alerter, ctx.admin_texts, error, action)
    user = _private_sender(event)
    if user is None:
        return True
    try:
        await ctx.delivery.send(user.id, ctx.screens.error())
    # Извинение не должно порождать новую ошибку в обработчике ошибок; сбой — в лог
    except TelegramAPIError:
        logger.exception("Не удалось извиниться перед клиентом %s", user.id)
    return True


def _handled(update: Update, texts: Texts) -> str:
    """Что обрабатывалось: вид кнопки, команда без аргументов или «сообщение»."""
    if update.callback_query is not None:
        query = update.callback_query
        # метку сохраняемого текста снимает промежуточный слой, а сюда апдейт приходит как есть
        data = (query.data or "").removeprefix(KEPT_TEXT_MARK)
        button = data.split(CALLBACK_SEPARATOR, 1)[0]
        chat = query.message.chat if query.message is not None else None
        return texts.get("bot_error_action.button", button=button, chat=_chat(chat, texts))
    if update.message is not None:
        message = update.message
        chat = _chat(message.chat, texts)
        words = (message.text or "").split(maxsplit=1)
        if words and words[0].startswith(COMMAND_START):
            command = words[0].split(BOT_NAME_SEPARATOR, 1)[0]
            return texts.get("bot_error_action.command", command=command, chat=chat)
        return texts.get("bot_error_action.message", chat=chat)
    return texts.get("bot_error_action.other", kind=update.event_type)


def _chat(chat: Chat | None, texts: Texts) -> str:
    private = chat is not None and chat.type == ChatType.PRIVATE
    return texts.get("bot_error_action.chat.private" if private else "bot_error_action.chat.group")
