"""«↩️ Вернуть деньги» у клиента и карточка заявки в «↩️ Возвраты» (задача 21).

Клиент: «↩️ Вернуть деньги» в профиле → условия и сумма → «✅ Отправить запрос» →
«запрос №N отправлен, опишите проблему» (обычное сообщение уходит в поддержку, как всегда).
Случайная вторая оплата: кнопку `double_payment_refund_button` к сообщению «получили две
оплаты» добавляет задача 14; нажатие → сумма → «✅ Да, вернуть вторую оплату» → запрос.

Кнопки клиента не несут ничего, что даёт доступ: чей возврат — по `from_user.id`, сумму
сервис считает заново при каждом нажатии.

Здесь же — реализации `RefundBoard` (карточка в теме с кнопками решения) и `RefundMessages`
(решение клиенту).
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.admin_refunds import RefundAdminScreens
from tsproxy.bot.callbacks import (
    DoublePaymentRefundCallback,
    MenuCallback,
    RefundRequestCallback,
    ScreenId,
)
from tsproxy.bot.connect import panel_unavailable_screen
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import Screen
from tsproxy.content.loader import Texts
from tsproxy.db.models import Client
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.domain.profile import plural_form
from tsproxy.domain.refund import RefundKind
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.refunds import (
    RefundCard,
    RefundQuote,
    RefundRefusal,
    RefundRefused,
    RefundRejection,
    RefundService,
)

logger = logging.getLogger(__name__)

# Что сделать по нажатию: посчитать сумму или отправить заявку
type RefundAction = Callable[[Client], Awaitable[RefundQuote | RefundCard | RefundRefused]]


def double_payment_refund_button(texts: Texts, order_id: int) -> InlineKeyboardButton:
    """Кнопка «↩️ Вернуть вторую оплату» для сообщения о двойной оплате (задача 14)."""
    return InlineKeyboardButton(
        text=texts.get("buttons.refund_double_payment"),
        callback_data=DoublePaymentRefundCallback(order_id=order_id).pack(),
    )


class RefundScreens:
    """Экраны возврата у клиента из `content/texts.yaml`."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _button(self, key: str, screen: ScreenId) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self._texts.get(key), callback_data=MenuCallback(screen=screen).pack()
        )

    def _days(self, days: int) -> str:
        return self._texts.get(f"units.days_{plural_form(days).value}", n=days)

    def _screen(self, text: str, *rows: list[InlineKeyboardButton]) -> Screen:
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=list(rows)))

    def quote(self, quote: RefundQuote) -> Screen:
        """Условия и сумма, кнопка «Отправить запрос»."""
        values = {
            "paid_at": format_moscow(quote.paid_at),
            "paid_amount": format_rubles(quote.paid_amount),
            "amount": format_rubles(quote.amount),
            "days": self._days(quote.unused_days),
        }
        if quote.kind is RefundKind.DOUBLE_PAYMENT:
            text = self._texts.get("refund.double_payment_quote", **values)
            confirm = InlineKeyboardButton(
                text=self._texts.get("buttons.confirm_refund_double_payment"),
                callback_data=DoublePaymentRefundCallback(
                    order_id=quote.order_id, confirm=True
                ).pack(),
            )
        else:
            text = self._texts.get("refund.quote", **values)
            confirm = InlineKeyboardButton(
                text=self._texts.get("buttons.confirm_refund"),
                callback_data=RefundRequestCallback().pack(),
            )
        return self._screen(
            text, [confirm], [self._button("buttons.profile_back", ScreenId.PROFILE)]
        )

    def refused(self, refused: RefundRefused, kind: RefundKind) -> Screen:
        """Почему вернуть нельзя — и куда идти дальше (поддержка, профиль)."""
        key = f"refund.{refused.refusal.value}"
        if kind is RefundKind.DOUBLE_PAYMENT and refused.refusal is RefundRefusal.WINDOW_PASSED:
            key = "refund.double_payment_window_passed"
        values = {}
        if refused.available_at is not None:
            values["available_at"] = format_moscow(refused.available_at)
        return self._screen(
            self._texts.get(key, **values),
            [self._button("buttons.support", ScreenId.SUPPORT)],
            [self._button("buttons.profile_back", ScreenId.PROFILE)],
            [self._button("buttons.back", ScreenId.MAIN)],
        )

    def requested(self, card: RefundCard) -> Screen:
        return self._screen(
            self._texts.get("refund.requested", request_id=card.request_id),
            [self._button("buttons.back", ScreenId.MAIN)],
        )

    def completed(self, amount: Decimal, expire_at: datetime | None) -> Screen:
        if expire_at is None:
            text = self._texts.get("refund.completed_access_ended", amount=format_rubles(amount))
            return self._screen(text, [self._button("buttons.tariffs", ScreenId.TARIFFS)])
        text = self._texts.get(
            "refund.completed", amount=format_rubles(amount), expire_at=format_moscow(expire_at)
        )
        return self._screen(text, [self._button("buttons.profile", ScreenId.PROFILE)])

    def rejected(self, reason: RefundRejection) -> Screen:
        return self._screen(
            self._texts.get(f"refund.rejected.{reason.value}"),
            [self._button("buttons.support", ScreenId.SUPPORT)],
        )


class TelegramRefundMessages:
    """Решение по возврату — клиенту новым сообщением (`RefundMessages`).

    Решение к этому моменту уже записано, поэтому сбой Telegram пишется в лог, а не
    бросается: иначе фоновая задача повторяла бы уже сделанный возврат.
    """

    def __init__(self, delivery: ClientDelivery, screens: RefundScreens) -> None:
        self._delivery = delivery
        self._screens = screens

    async def refund_completed(
        self, telegram_id: int, amount: Decimal, expire_at: datetime | None
    ) -> None:
        await self._send(telegram_id, self._screens.completed(amount, expire_at))

    async def refund_rejected(self, telegram_id: int, reason: RefundRejection) -> None:
        await self._send(telegram_id, self._screens.rejected(reason))

    async def _send(self, telegram_id: int, screen: Screen) -> None:
        try:
            await self._delivery.send(telegram_id, screen)
        except TelegramAPIError:
            logger.exception("Не удалось прислать решение по возврату клиенту %s", telegram_id)


class TelegramRefundBoard:
    """Карточка новой заявки с кнопками решения — в тему «↩️ Возвраты» (`RefundBoard`).

    Ошибку Telegram не прячет: сервис поднимет тревогу «заявка не отправлена».
    """

    def __init__(
        self, bot: Bot, screens: RefundAdminScreens, chat_id: int, thread_id: int | None
    ) -> None:
        self._bot = bot
        self._screens = screens
        self._chat_id = chat_id
        self._thread_id = thread_id

    async def request_created(self, card: RefundCard) -> None:
        screen = self._screens.card(card)
        await self._bot.send_message(
            self._chat_id,
            screen.text,
            message_thread_id=self._thread_id,
            reply_markup=screen.markup,
        )


def create_refunds_router(refunds: RefundService) -> Router:
    """Кнопки возврата у клиента — только в личном чате с ботом."""
    router = Router(name="refunds")
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

    async def show(
        callback: CallbackQuery,
        ctx: BotContext,
        kind: RefundKind,
        action: RefundAction,
    ) -> None:
        await callback.answer()
        client = await ctx.clients.resolve(profile_of(callback.from_user))
        screens = RefundScreens(ctx.texts)
        try:
            result = await action(client)
        except RemnawaveUnavailable as error:
            screen = await panel_unavailable_screen(ctx, client.telegram_id, error)
        else:
            match result:
                case RefundQuote():
                    screen = screens.quote(result)
                case RefundCard():
                    screen = screens.requested(result)
                case RefundRefused():
                    screen = screens.refused(result, kind)
        await ctx.delivery.show(callback, screen)

    async def on_refund(callback: CallbackQuery, ctx: BotContext) -> None:
        """«↩️ Вернуть деньги» — условия и сумма за последнюю оплату."""
        await show(callback, ctx, RefundKind.STANDARD, refunds.quote)

    async def on_request(callback: CallbackQuery, ctx: BotContext) -> None:
        """«✅ Отправить запрос» — заявка в «↩️ Возвраты»."""
        await show(callback, ctx, RefundKind.STANDARD, refunds.request)

    async def on_double_payment(
        callback: CallbackQuery, callback_data: DoublePaymentRefundCallback, ctx: BotContext
    ) -> None:
        order_id = callback_data.order_id

        async def action(client: Client) -> RefundQuote | RefundCard | RefundRefused:
            if callback_data.confirm:
                return await refunds.request_double_payment(client, order_id)
            return await refunds.quote_double_payment(client, order_id)

        await show(callback, ctx, RefundKind.DOUBLE_PAYMENT, action)

    router.callback_query.register(on_refund, MenuCallback.filter(F.screen == ScreenId.REFUND))
    router.callback_query.register(on_request, RefundRequestCallback.filter())
    router.callback_query.register(on_double_payment, DoublePaymentRefundCallback.filter())
    return router
