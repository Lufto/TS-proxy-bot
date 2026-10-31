"""Возвраты у клиента (задача 21, решение владельца 2026-10-30).

Кнопки «вернуть деньги» у клиента нет — возвраты через поддержку (`bot.admin_refunds`).
Исключение — случайная вторая оплата: под сообщением «мы получили две оплаты» (`bot.payments`)
кнопки «↩️ Вернуть вторую оплату» → сумма и «✅ Да, вернуть» → возврат без администратора,
и «Оставить — продлить подписку». Итог показывается в том же сообщении.

Номер заказа в кнопке — не доступ: чей возврат, решает `from_user.id`, а сервис ищет заказ
только среди заказов нажавшего и только с отметкой «двойная оплата».

Здесь же — `TelegramRefundMessages`: сообщение клиенту о законченном возврате.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.callbacks import (
    DoublePaymentAction,
    DoublePaymentCallback,
    MenuCallback,
    ScreenId,
)
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import Screen
from tsproxy.content.amounts import paid_text
from tsproxy.content.loader import Texts
from tsproxy.domain.constants import ONE_DAY
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.profile import plural_form
from tsproxy.services.refunds import (
    RefundablePayment,
    RefundRefusal,
    RefundRefused,
    RefundResult,
)

logger = logging.getLogger(__name__)

ONE_HOUR = timedelta(hours=1)
# Отказы, у которых свой текст; остальные — «нельзя кнопкой, напишите в поддержку»
OWN_REFUSAL_TEXTS = (
    RefundRefusal.WINDOW_PASSED,
    RefundRefusal.KEPT,
    RefundRefusal.ALREADY_REFUNDED,
)


class RefundScreens:
    """Экраны возврата у клиента из `content/texts.yaml`."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _button(self, key: str, screen: ScreenId) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self._texts.get(key), callback_data=MenuCallback(screen=screen).pack()
        )

    def _screen(self, text: str, *rows: list[InlineKeyboardButton]) -> Screen:
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=list(rows)))

    def _days(self, days: int) -> str:
        return self._texts.get(f"units.days_{plural_form(days).value}", n=days)

    def _duration(self, duration: timedelta) -> str:
        days, rest = divmod(duration, ONE_DAY)
        hours = rest // ONE_HOUR
        if hours == 0:
            return self._days(days)
        return self._texts.get("refund.duration_hours", days=self._days(days), hours=hours)

    def double_ask(self, payment: RefundablePayment) -> Screen:
        """Сумма и «точно вернуть?»: «✅ Да, вернуть» и «Оставить»."""
        amount = paid_text(self._texts, payment.amount, payment.stars)
        text = self._texts.get(
            "refund.double_ask",
            amount=amount,
            paid_at=format_moscow(payment.paid_at),
            duration=self._duration(payment.duration),
        )
        confirm = InlineKeyboardButton(
            text=self._texts.get("buttons.confirm_refund_double_payment", amount=amount),
            callback_data=DoublePaymentCallback(
                action=DoublePaymentAction.CONFIRM, order_id=payment.order_id
            ).pack(),
        )
        keep = InlineKeyboardButton(
            text=self._texts.get("buttons.keep_double_payment"),
            callback_data=DoublePaymentCallback(
                action=DoublePaymentAction.KEEP, order_id=payment.order_id
            ).pack(),
        )
        return self._screen(text, [confirm], [keep])

    def double_progress(self, result: RefundResult) -> Screen:
        return self._screen(
            self._texts.get(f"refund.double_progress.{result.progress.value}"),
            [self._button("buttons.profile", ScreenId.PROFILE)],
        )

    def double_kept(self) -> Screen:
        return self._screen(
            self._texts.get("refund.double_kept"),
            [self._button("buttons.profile", ScreenId.PROFILE)],
        )

    def double_refused(self, refused: RefundRefused) -> Screen:
        key = refused.refusal.value if refused.refusal in OWN_REFUSAL_TEXTS else "other"
        return self._screen(
            self._texts.get(f"refund.double_refused.{key}"),
            [self._button("buttons.support", ScreenId.SUPPORT)],
            [self._button("buttons.back", ScreenId.MAIN)],
        )

    def completed(self, amount: Decimal, expire_at: datetime | None, stars: int | None) -> Screen:
        refunded = paid_text(self._texts, amount, stars)
        if expire_at is None:
            text = self._texts.get("refund.completed_access_ended", amount=refunded)
            return self._screen(text, [self._button("buttons.tariffs", ScreenId.TARIFFS)])
        text = self._texts.get(
            "refund.completed", amount=refunded, expire_at=format_moscow(expire_at)
        )
        return self._screen(text, [self._button("buttons.profile", ScreenId.PROFILE)])


class TelegramRefundMessages:
    """Возврат закончен — клиенту новым сообщением (`services.refunds.RefundMessages`).

    Возврат к этому моменту уже сделан, поэтому сбой Telegram пишется в лог, а не
    бросается: иначе фоновая задача считала бы возврат незаконченным.
    """

    def __init__(self, delivery: ClientDelivery, screens: RefundScreens) -> None:
        self._delivery = delivery
        self._screens = screens

    async def refund_completed(
        self, telegram_id: int, amount: Decimal, expire_at: datetime | None, *, stars: int | None
    ) -> None:
        screen = self._screens.completed(amount, expire_at, stars)
        try:
            await self._delivery.send(telegram_id, screen)
        except TelegramAPIError:
            logger.exception("Не удалось сообщить клиенту %s о возврате", telegram_id)


def create_refunds_router() -> Router:
    """Кнопки «двойной оплаты» — только в личном чате с ботом."""
    router = Router(name="refunds")
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.callback_query.register(on_double_payment, DoublePaymentCallback.filter())
    return router


async def on_double_payment(
    callback: CallbackQuery, callback_data: DoublePaymentCallback, ctx: BotContext
) -> None:
    await callback.answer()
    telegram_id, order_id = callback.from_user.id, callback_data.order_id
    screens = RefundScreens(ctx.texts)
    match callback_data.action:
        case DoublePaymentAction.ASK:
            payment = await ctx.refunds.double_payment(telegram_id, order_id)
            screen = (
                screens.double_refused(payment)
                if isinstance(payment, RefundRefused)
                else screens.double_ask(payment)
            )
        case DoublePaymentAction.CONFIRM:
            result = await ctx.refunds.refund_double_payment(telegram_id, order_id)
            screen = (
                screens.double_refused(result)
                if isinstance(result, RefundRefused)
                else screens.double_progress(result)
            )
        case DoublePaymentAction.KEEP:
            refused = await ctx.refunds.keep_double_payment(telegram_id, order_id)
            screen = screens.double_kept() if refused is None else screens.double_refused(refused)
    await ctx.delivery.show(callback, screen)
