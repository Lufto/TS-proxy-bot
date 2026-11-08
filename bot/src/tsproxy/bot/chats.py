"""Чаты, в которых оказался бот: выход из чужих групп (аудит R19) и «клиент заблокировал бота».

Бот работает только в личных чатах с клиентами и в группе администраторов. Если его
добавили в любую другую группу или канал (или он там уже был) — выходит.
"""

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import Chat, ChatMemberUpdated, Message

from tsproxy.bot.admin import ForeignChatFilter
from tsproxy.bot.context import BotContext

logger = logging.getLogger(__name__)

# Статусы, в которых бот присутствует в чате
PRESENT_STATUSES = frozenset(
    {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.RESTRICTED}
)


def create_chats_router(foreign_chat: ForeignChatFilter) -> Router:
    """Роутер чатов. Фильтр «чужой чат» знает, какая группа — администраторов."""
    router = Router(name="chats")
    router.my_chat_member.register(on_added_to_foreign_chat, foreign_chat)
    router.my_chat_member.register(
        on_private_status_changed,
        F.chat.type == ChatType.PRIVATE,
        F.new_chat_member.status == ChatMemberStatus.KICKED,
    )
    router.message.register(on_foreign_chat_message, foreign_chat)
    router.channel_post.register(on_foreign_chat_message, foreign_chat)
    return router


async def _leave(bot: Bot, chat: Chat) -> None:
    logger.warning("Бот в чужом чате %s (%s) — выходим", chat.id, chat.type)
    await bot.leave_chat(chat.id)


async def on_added_to_foreign_chat(event: ChatMemberUpdated, bot: Bot) -> None:
    """Бота добавили в чужую группу или канал."""
    if event.new_chat_member.status in PRESENT_STATUSES:
        await _leave(bot, event.chat)


async def on_foreign_chat_message(message: Message, bot: Bot) -> None:
    """Сообщение из чужой группы или канала: бот там оказался раньше — выходим."""
    await _leave(bot, message.chat)


async def on_private_status_changed(event: ChatMemberUpdated, ctx: BotContext) -> None:
    """Клиент нажал «Заблокировать» в личном чате с ботом."""
    await ctx.clients.mark_blocked(event.chat.id)
