"""«↩️ Вернуть» в группе администраторов (задача 21, решение владельца 2026-10-30).

Клиент пишет в поддержку, администратор решает сам: «↩️ Вернуть» в карточке клиента (тема
обращения или `/client`) → оплаты за последние 30 дней → выбор оплаты → «✅ Да, вернуть» →
вся сумма через платёжку, дни этой оплаты снимаются, клиенту — сообщение. Итог заменяет
вопрос в той же теме; «на проверке у платёжки» — тоже туда. В журнал — с именем
администратора (`services.refunds`).

Права: каждый обработчик за `AdminFilter`; номер заказа в кнопке — не доступ, сервис ищет
заказ только среди заказов этого клиента.
"""

import logging

from aiogram import Bot, Router
from aiogram.types import CallbackQuery

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_callbacks import AdminRefundCallback
from tsproxy.bot.admin_clients import on_foreign_press, replace_screen, send_from
from tsproxy.bot.admin_refund_screens import RefundAdminScreens, refund_list_screen
from tsproxy.bot.context import BotContext
from tsproxy.services.refunds import AdminRef, RefundRefused

logger = logging.getLogger(__name__)


def create_admin_refunds_router(access: AdminAccess) -> Router:
    router = Router(name="admin_refunds")
    router.callback_query.register(on_refund, AdminFilter(access), AdminRefundCallback.filter())
    # Сюда доходят только нажатия, которые не прошли `AdminFilter`
    router.callback_query.register(on_foreign_press, AdminRefundCallback.filter())
    return router


async def on_refund(
    callback: CallbackQuery, callback_data: AdminRefundCallback, bot: Bot, ctx: BotContext
) -> None:
    """Список → вопрос «вернуть?» → возврат."""
    await callback.answer()
    telegram_id, order_id = callback_data.telegram_id, callback_data.order_id
    screens = RefundAdminScreens(ctx.admin_texts)
    if order_id == 0:
        await send_from(callback, bot, ctx, await refund_list_screen(ctx, telegram_id))
        return
    if not callback_data.confirm:
        payment = await ctx.refunds.payment(telegram_id, order_id)
        if isinstance(payment, RefundRefused):
            await send_from(callback, bot, ctx, screens.refused(telegram_id, order_id, payment))
            return
        await send_from(callback, bot, ctx, screens.ask(telegram_id, payment))
        return
    admin = AdminRef(callback.from_user.id, callback.from_user.full_name)
    result = await ctx.refunds.refund_by_admin(admin, telegram_id, order_id)
    if isinstance(result, RefundRefused):
        screen = screens.refused(telegram_id, order_id, result)
    else:
        logger.info(
            "Администратор %s: возврат %s заказа %s — %s",
            admin.telegram_id,
            result.request_id,
            order_id,
            result.progress.value,
        )
        screen = screens.result(result)
    await replace_screen(callback, bot, ctx, screen)
