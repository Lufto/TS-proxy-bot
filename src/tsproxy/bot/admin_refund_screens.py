"""Сообщения «↩️ Вернуть» для администраторов (задача 21): список оплат клиента за 30 дней,
вопрос «вернуть?», итог. Только сборка из `content/admin_texts.yaml`, без вызовов Telegram.

Кнопку «↩️ Вернуть» ставят карточка `/client` (`admin_screens.refund_button`) и карточка
клиента в теме обращения (`support_card`); нажатия обрабатывает `bot.admin_refunds`.
"""

from datetime import timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.admin_callbacks import AdminAction, AdminClientCallback, AdminRefundCallback
from tsproxy.bot.admin_screens import AdminScreen
from tsproxy.bot.context import BotContext
from tsproxy.content.loader import Texts
from tsproxy.domain.constants import ONE_DAY
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.services.refunds import (
    RefundablePayment,
    RefundProgress,
    RefundRefusal,
    RefundRefused,
    RefundResult,
)

LINE = "\n"
PARAGRAPH = "\n\n"
ONE_HOUR = timedelta(hours=1)


class RefundAdminScreens:
    """Собирает сообщения «↩️ Вернуть» из текстов администраторов."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def payments(
        self, telegram_id: int, name: str | None, payments: list[RefundablePayment]
    ) -> AdminScreen:
        """Оплаты за 30 дней: что с каждой и кнопка у тех, что можно вернуть."""
        if not payments:
            return AdminScreen(self._texts.get("refunds.list.empty", telegram_id=telegram_id))
        title = self._texts.get(
            "refunds.list.title",
            name=name or self._texts.get("admin.no_value"),
            telegram_id=telegram_id,
        )
        items = [self._item(payment) for payment in payments]
        buttons = [
            [
                InlineKeyboardButton(
                    text=self._texts.get(
                        "refunds.buttons.order",
                        order_id=payment.order_id,
                        amount=format_rubles(payment.amount),
                    ),
                    callback_data=AdminRefundCallback(
                        telegram_id=telegram_id, order_id=payment.order_id
                    ).pack(),
                )
            ]
            for payment in payments
            if payment.blocker is None
        ]
        markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        return AdminScreen(PARAGRAPH.join([title, LINE.join(items)]), markup)

    def _item(self, payment: RefundablePayment) -> str:
        return self._texts.get(
            "refunds.list.item",
            order_id=payment.order_id,
            paid_at=format_moscow(payment.paid_at),
            tariff_id=payment.tariff_id,
            days=payment.days,
            amount=format_rubles(payment.amount),
            state=self._state(payment),
        )

    def _state(self, payment: RefundablePayment) -> str:
        blocker = payment.blocker
        if blocker is None:
            return self._texts.get("refunds.state.available")
        if blocker is RefundRefusal.ALREADY_REFUNDED and payment.refund_status is not None:
            return self._texts.get(f"refunds.state.{payment.refund_status.value}")
        if blocker in (
            RefundRefusal.NOT_GRANTED,
            RefundRefusal.TRANSFERRED,
            RefundRefusal.PAYMENTS_OFF,
        ):
            return self._texts.get(f"refunds.state.{blocker.value}")
        return self._texts.get(f"refunds.refused.{blocker.value}", order_id=payment.order_id)

    def ask(self, telegram_id: int, payment: RefundablePayment) -> AdminScreen:
        """«Вернуть … ₽? Со срока снимется …» — «✅ Да, вернуть» и «Отмена»."""
        amount = format_rubles(payment.amount)
        text = self._texts.get(
            "refunds.ask",
            telegram_id=telegram_id,
            amount=amount,
            order_id=payment.order_id,
            paid_at=format_moscow(payment.paid_at),
            tariff_id=payment.tariff_id,
            duration=self._duration(payment.duration),
        )
        confirm = InlineKeyboardButton(
            text=self._texts.get("refunds.buttons.confirm", amount=amount),
            callback_data=AdminRefundCallback(
                telegram_id=telegram_id, order_id=payment.order_id, confirm=True
            ).pack(),
        )
        cancel = InlineKeyboardButton(
            text=self._texts.get("admin.buttons.cancel"),
            callback_data=AdminClientCallback(
                action=AdminAction.CANCEL, telegram_id=telegram_id
            ).pack(),
        )
        return AdminScreen(text, InlineKeyboardMarkup(inline_keyboard=[[confirm], [cancel]]))

    def _duration(self, duration: timedelta) -> str:
        days, rest = divmod(duration, ONE_DAY)
        hours = rest // ONE_HOUR
        if hours == 0:
            return self._texts.get("refunds.duration", days=days)
        return self._texts.get("refunds.duration_hours", days=days, hours=hours)

    def result(self, result: RefundResult) -> AdminScreen:
        """Итог возврата — заменяет вопрос в той же теме."""
        key = f"refunds.result.{result.progress.value}"
        if result.progress is RefundProgress.COMPLETED and result.expire_at is None:
            key = "refunds.result.completed_access_ended"
        values: dict[str, object] = {
            "request_id": result.request_id,
            "amount": format_rubles(result.amount),
            "telegram_id": result.telegram_id,
        }
        if result.expire_at is not None:
            values["expire_at"] = format_moscow(result.expire_at)
        return AdminScreen(self._texts.get(key, **values))

    def refused(self, telegram_id: int, order_id: int, refused: RefundRefused) -> AdminScreen:
        return AdminScreen(
            self._texts.get(
                f"refunds.refused.{refused.refusal.value}",
                telegram_id=telegram_id,
                order_id=order_id,
            )
        )


async def refund_list_screen(ctx: BotContext, telegram_id: int) -> AdminScreen:
    """Список «↩️ Вернуть» клиента: из базы, панель не нужна."""
    screens = RefundAdminScreens(ctx.admin_texts)
    payments = await ctx.refunds.refundable_payments(telegram_id)
    if payments is None:
        return screens.refused(telegram_id, 0, RefundRefused(RefundRefusal.CLIENT_NOT_FOUND))
    client = await ctx.clients.find(telegram_id)
    return screens.payments(telegram_id, None if client is None else client.name, payments)
