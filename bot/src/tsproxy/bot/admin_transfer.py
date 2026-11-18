"""`/transfer 111 222 номер_платежа` — перенос подписки на другой Telegram-аккаунт (задача 21).

Только с подтверждением платежа: номер платежа из чека клиента должен совпасть с оплаченным
заказом старого аккаунта (`services.transfer`). Сначала предпросмотр «сейчас / станет»,
потом «✅ Да, перенести» — кнопка несёт сроки обоих аккаунтов из предпросмотра: если они
уже другие (двойное нажатие, оплата), ничего не делается. Новому аккаунту — сообщение.

Права — как у всей админки (`AdminFilter`).
"""

import logging
from datetime import timedelta

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_callbacks import (
    AdminAction,
    AdminClientCallback,
    AdminTransferCallback,
    expire_stamp,
    stamp_expire,
)
from tsproxy.bot.admin_clients import on_foreign_press, replace_screen
from tsproxy.bot.admin_screens import AdminScreen
from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.connect import PANEL_DOWN_ERRORS
from tsproxy.bot.context import BotContext
from tsproxy.bot.screens import Screen
from tsproxy.content.dates import client_time
from tsproxy.content.loader import Html, Texts
from tsproxy.domain.admin import parse_client_query
from tsproxy.domain.constants import ONE_DAY
from tsproxy.domain.dates import format_moscow
from tsproxy.services.transfer import (
    TransferDone,
    TransferPreview,
    TransferRefused,
    TransferService,
)

logger = logging.getLogger(__name__)

TRANSFER_COMMAND = "transfer"
# `/transfer <откуда> <куда> <номер платежа>`
TRANSFER_ARGUMENTS = 3
ONE_HOUR = timedelta(hours=1)


class TransferScreens:
    """Сообщения переноса из `content/admin/`."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _text(self, key: str, **values: object) -> AdminScreen:
        return AdminScreen(self._texts.get(f"admin.transfer.{key}", **values))

    def usage(self) -> AdminScreen:
        return self._text("usage")

    def panel_unavailable(self, reason: Html) -> AdminScreen:
        """Панель не выполнила запрос; `reason` — почему и что делать (`Alerter.explain`)."""
        return AdminScreen(self._texts.get("admin.panel_unavailable", reason=reason))

    def preview(self, preview: TransferPreview) -> AdminScreen:
        no_subscription = self._texts.get("admin.transfer.no_subscription")
        days, rest = divmod(preview.left, ONE_DAY)
        text = self._texts.get(
            "admin.transfer.preview",
            from_id=preview.from_telegram_id,
            to_id=preview.to_telegram_id,
            from_expire=format_moscow(preview.from_expire_at),
            to_expire=(
                no_subscription
                if preview.to_expire_at is None
                else format_moscow(preview.to_expire_at)
            ),
            payment_id=preview.payment_id,
            order_id=preview.order_id,
            left=self._texts.get("admin.transfer.left", days=days, hours=rest // ONE_HOUR),
            new_expire=format_moscow(preview.new_expire_at),
        )
        confirm = InlineKeyboardButton(
            text=self._texts.get("admin.transfer.confirm"),
            callback_data=AdminTransferCallback(
                to_telegram_id=preview.to_telegram_id,
                order_id=preview.order_id,
                from_stamp=expire_stamp(preview.from_expire_at),
                to_stamp=expire_stamp(preview.to_expire_at),
            ).pack(),
        )
        cancel = InlineKeyboardButton(
            text=self._texts.get("admin.buttons.cancel"),
            callback_data=AdminClientCallback(
                action=AdminAction.CANCEL, telegram_id=preview.to_telegram_id
            ).pack(),
        )
        return AdminScreen(text, InlineKeyboardMarkup(inline_keyboard=[[confirm], [cancel]]))

    def refused(self, refused: TransferRefused, **values: object) -> AdminScreen:
        return self._text(refused.refusal.value, **values)

    def done(self, from_id: int, to_id: int, done: TransferDone) -> AdminScreen:
        return self._text(
            "done", from_id=from_id, to_id=to_id, expire_at=format_moscow(done.to_expire_at)
        )


def create_admin_transfer_router(access: AdminAccess, transfers: TransferService) -> Router:
    router = Router(name="admin_transfer")
    admin = AdminFilter(access)

    async def on_transfer_command(
        message: Message, command: CommandObject, ctx: BotContext
    ) -> None:
        """Предпросмотр переноса. Только по Telegram ID обоих аккаунтов."""
        screens = TransferScreens(ctx.admin_texts)
        parts = (command.args or "").split()
        if len(parts) != TRANSFER_ARGUMENTS:
            await message.answer(screens.usage().text)
            return
        source, target = parse_client_query(parts[0]), parse_client_query(parts[1])
        from_id = None if source is None else source.telegram_id
        to_id = None if target is None else target.telegram_id
        if from_id is None or to_id is None:
            await message.answer(screens.usage().text)
            return
        payment_id = parts[2]
        try:
            preview = await transfers.preview(from_id, to_id, payment_id)
        except PANEL_DOWN_ERRORS as error:
            logger.warning("Перенос %s → %s: панель не ответила: %s", from_id, to_id, error)
            await message.answer(screens.panel_unavailable(ctx.alerter.explain(error)).text)
            return
        if isinstance(preview, TransferRefused):
            screen = screens.refused(preview, from_id=from_id, to_id=to_id, payment_id=payment_id)
        else:
            screen = screens.preview(preview)
        await message.answer(screen.text, reply_markup=screen.markup)

    async def on_confirm(
        callback: CallbackQuery, callback_data: AdminTransferCallback, bot: Bot, ctx: BotContext
    ) -> None:
        """«✅ Да, перенести» — если сроки обоих аккаунтов те же, что в предпросмотре."""
        await callback.answer()
        screens = TransferScreens(ctx.admin_texts)
        to_id = callback_data.to_telegram_id
        from_expire = stamp_expire(callback_data.from_stamp)
        if from_expire is None:
            await replace_screen(callback, bot, ctx, screens.usage())
            return
        try:
            result = await transfers.transfer(
                callback.from_user.id,
                callback_data.order_id,
                to_id,
                expected_from=from_expire,
                expected_to=stamp_expire(callback_data.to_stamp),
            )
        except PANEL_DOWN_ERRORS as error:
            logger.warning("Перенос на %s: панель не ответила: %s", to_id, error)
            await replace_screen(
                callback, bot, ctx, screens.panel_unavailable(ctx.alerter.explain(error))
            )
            return
        if isinstance(result, TransferRefused):
            from_id = await transfers.order_owner(callback_data.order_id)
            screen = screens.refused(
                result, from_id=from_id or callback_data.order_id, to_id=to_id, payment_id=""
            )
        else:
            screen = screens.done(result.from_telegram_id, to_id, result)
            await _tell_new_account(ctx, to_id, result)
        await replace_screen(callback, bot, ctx, screen)

    router.message.register(on_transfer_command, admin, Command(TRANSFER_COMMAND))
    router.callback_query.register(on_confirm, admin, AdminTransferCallback.filter())
    router.callback_query.register(on_foreign_press, AdminTransferCallback.filter())
    return router


async def _tell_new_account(ctx: BotContext, to_id: int, done: TransferDone) -> None:
    """Новому аккаунту — «подписка перенесена, добавьте ссылку». Сбой Telegram — только лог."""
    connect = InlineKeyboardButton(
        text=ctx.texts.get("buttons.connect"),
        callback_data=MenuCallback(screen=ScreenId.CONNECT).pack(),
    )
    screen = Screen(
        ctx.texts.get("transfer.received", expire_at=client_time(done.to_expire_at)),
        InlineKeyboardMarkup(inline_keyboard=[[connect]]),
    )
    try:
        await ctx.delivery.send(to_id, screen)
    except TelegramAPIError:
        logger.exception("Перенос: не удалось сообщить новому аккаунту %s", to_id)
