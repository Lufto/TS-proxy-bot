"""«Печатает…» в шапке чата клиента, пока бот ждёт панель или платёжку.

Обработчик, который ходит в Remnawave или Platega, помечается флагом `TYPING`
(`router.callback_query.register(..., flags=TYPING)`). Перед таким обработчиком бот
отправляет клиенту `sendChatAction typing` и повторяет его, пока обработчик работает:
Telegram держит «печатает…» около 5 секунд или до первого сообщения бота, а панель
и платёжка могут думать дольше (до 10–15 секунд).

«Печатает…» — только украшение: если Telegram его не принял, действие клиента всё равно
выполняется, ошибка лишь пишется в журнал.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.dispatcher.flags import get_flag
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Chat, TelegramObject

logger = logging.getLogger(__name__)

TYPING_FLAG = "typing"
TYPING = {TYPING_FLAG: True}  # флаги обработчика: показать «печатает…»
# Telegram гасит «печатает…» через 5 секунд — повторяем чуть раньше
TYPING_REPEAT_SECONDS = 4.5


async def _send_typing(bot: Bot, chat_id: int) -> None:
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except TelegramAPIError as error:
        logger.warning("Не удалось показать «печатает…» в чате %s: %s", chat_id, error)


async def _repeat_typing(bot: Bot, chat_id: int) -> None:
    while True:
        await asyncio.sleep(TYPING_REPEAT_SECONDS)
        await _send_typing(bot, chat_id)


class TypingMiddleware(BaseMiddleware):
    """Показывает «печатает…» на время обработчиков с флагом `TYPING`."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat: Chat | None = data.get("event_chat")
        if not get_flag(data, TYPING_FLAG) or chat is None:
            return await handler(event, data)
        bot: Bot = data["bot"]
        await _send_typing(bot, chat.id)
        repeater = asyncio.create_task(_repeat_typing(bot, chat.id))
        try:
            return await handler(event, data)
        finally:
            repeater.cancel()
