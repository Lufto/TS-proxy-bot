"""«🗑 Удалить данные клиента» в карточке админки (задача 24).

Кнопка в карточке → предпросмотр «что удалится и что останется» с «✅ Да, удалить данные»
→ удаление (`DataErasureService`). Итог заменяет сообщение с кнопкой подтверждения, чтобы
её нельзя было нажать ещё раз; при сбое удаления темы сообщение с кнопкой остаётся — повтор
безопасен.

Права — как у всей админки: `AdminFilter` на каждом обработчике (администратор и группа
администраторов); чужое нажатие — всплывающий отказ, ничего не удаляется.
"""

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_callbacks import AdminAction, AdminClientCallback, AdminEraseCallback
from tsproxy.bot.admin_clients import on_foreign_press
from tsproxy.bot.admin_screens import AdminScreen
from tsproxy.bot.context import BotContext
from tsproxy.content.loader import Texts
from tsproxy.services.erasure import (
    ErasureOutcome,
    ErasurePreview,
    ErasureResult,
    TopicRemovalFailed,
)

logger = logging.getLogger(__name__)

# Так Telegram отвечает на удаление темы, которой уже нет (удалили руками или прошлой попыткой)
TOPIC_GONE_MARKERS = ("topic_id_invalid", "thread not found")


class TelegramTopicRemover:
    """Удаляет темы обращений в группе администраторов (`TopicRemover` для сервиса)."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def remove(self, topic_id: int) -> None:
        try:
            await self._bot.delete_forum_topic(self._chat_id, topic_id)
        except TelegramBadRequest as error:
            if any(marker in error.message.lower() for marker in TOPIC_GONE_MARKERS):
                logger.info("Тема %s уже удалена: %s", topic_id, error.message)
                return
            raise TopicRemovalFailed(error.message) from error
        except TelegramAPIError as error:
            # нет прав, бота убрали из группы, сеть — удаление данных не продолжаем
            raise TopicRemovalFailed(error.message) from error


def create_admin_erasure_router(access: AdminAccess) -> Router:
    router = Router(name="admin_erasure")
    router.callback_query.register(on_erase, AdminFilter(access), AdminEraseCallback.filter())
    # Сюда доходят только нажатия, которые не прошли `AdminFilter`
    router.callback_query.register(on_foreign_press, AdminEraseCallback.filter())
    return router


class ErasureScreens:
    """Сообщения удаления данных из `content/admin_texts.yaml`."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _or_none(self, value: str | None) -> str:
        return value or self._texts.get("admin.no_value")

    def ask(self, preview: ErasurePreview) -> AdminScreen:
        texts = self._texts
        text = texts.get(
            "admin.erase.ask",
            telegram_id=preview.telegram_id,
            name=self._or_none(preview.name),
            username=self._or_none(preview.username),
            tickets=preview.tickets,
            messages=preview.messages,
            topics=preview.topics,
            orders=preview.orders,
        )
        confirm = InlineKeyboardButton(
            text=texts.get("admin.buttons.confirm_erase"),
            callback_data=AdminEraseCallback(telegram_id=preview.telegram_id, confirm=True).pack(),
        )
        cancel = InlineKeyboardButton(
            text=texts.get("admin.buttons.cancel"),
            callback_data=AdminClientCallback(
                action=AdminAction.CANCEL, telegram_id=preview.telegram_id
            ).pack(),
        )
        return AdminScreen(text, InlineKeyboardMarkup(inline_keyboard=[[confirm], [cancel]]))

    def result(self, telegram_id: int, result: ErasureResult) -> AdminScreen:
        match result.outcome:
            case ErasureOutcome.ERASED:
                key = "admin.erase.done"
            case ErasureOutcome.NOTHING_TO_ERASE:
                key = "admin.erase.nothing"
            case ErasureOutcome.CLIENT_NOT_FOUND:
                key = "admin.client_not_found"
        return AdminScreen(
            self._texts.get(
                key,
                telegram_id=telegram_id,
                tickets=result.tickets,
                messages=result.messages,
                topics=result.topics,
            )
        )

    def client_not_found(self, telegram_id: int) -> AdminScreen:
        return AdminScreen(self._texts.get("admin.client_not_found", telegram_id=telegram_id))

    def topic_failed(self, error: str) -> AdminScreen:
        return AdminScreen(self._texts.get("admin.erase.topic_failed", error=error))


async def on_erase(
    callback: CallbackQuery, callback_data: AdminEraseCallback, bot: Bot, ctx: BotContext
) -> None:
    """«🗑 Удалить данные» → предпросмотр; «✅ Да, удалить данные» → удаление."""
    await callback.answer()
    screens = ErasureScreens(ctx.admin_texts)
    telegram_id = callback_data.telegram_id
    if not callback_data.confirm:
        preview = await ctx.erasure.preview(telegram_id)
        screen = screens.client_not_found(telegram_id) if preview is None else screens.ask(preview)
        await _send_from(callback, bot, ctx, screen)
        return
    try:
        # пока удаляем, новые сообщения клиента в поддержку ждут: иначе они открыли бы тему
        # в обращении, которое сейчас удаляется
        async with ctx.support_guard.lock(telegram_id):
            result = await ctx.erasure.erase(callback.from_user.id, telegram_id)
    except TopicRemovalFailed as error:
        logger.warning("Удаление данных клиента %s: тема не удалилась: %s", telegram_id, error)
        await _send_from(callback, bot, ctx, screens.topic_failed(str(error)))
        return
    screen = screens.result(telegram_id, result)
    message = callback.message
    if not isinstance(message, Message):
        await _send_from(callback, bot, ctx, screen)
    elif message.message_thread_id in result.topic_ids:
        # нажали в теме самого обращения — её больше нет, итог пишем в группу без темы
        await bot.send_message(ctx.access.admin_chat_id, screen.text)
    else:
        await message.edit_text(screen.text, reply_markup=screen.markup)


async def _send_from(
    callback: CallbackQuery, bot: Bot, ctx: BotContext, screen: AdminScreen
) -> None:
    """Новым сообщением рядом с нажатой кнопкой; старое сообщение — в группу без темы."""
    if isinstance(callback.message, Message):
        await callback.message.answer(screen.text, reply_markup=screen.markup)
        return
    await bot.send_message(ctx.access.admin_chat_id, screen.text, reply_markup=screen.markup)
