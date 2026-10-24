"""Всё, что не подошло ни одному обработчику, — подключается последним."""

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message

from tsproxy.bot.context import BotContext


def create_fallback_router() -> Router:
    """Подключается последним: сюда попадает только то, что не взял никто другой."""
    router = Router(name="fallback")
    router.callback_query.register(on_unknown_button, F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_unknown_message, F.chat.type == ChatType.PRIVATE)
    return router


async def on_unknown_button(callback: CallbackQuery, ctx: BotContext) -> None:
    """Кнопка от старой версии бота или поддельные данные: ничего не делаем, просим /start."""
    await callback.answer(ctx.texts.get("errors.stale_button"), show_alert=True)


async def on_unknown_message(message: Message, ctx: BotContext) -> None:
    """Обычное сообщение клиента. В задаче 16 оно станет обращением в поддержку."""
    if message.from_user is None:
        return
    await ctx.delivery.send(message.from_user.id, ctx.screens.unknown_message())
