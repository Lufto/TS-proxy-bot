"""Оплата звёздами Telegram в боте (задача 33).

- «⭐ Stars» на экране тарифа — экран меняется на «счёт ниже», следом приходит счёт
  (`sendInvoice`, валюта `XTR`, `provider_token` пустой, `payload` — номер заказа).
- `pre_checkout_query` — ответ «можно» или отказ с понятным текстом. Telegram ждёт его
  10 секунд, поэтому здесь только чтение из базы (`StarsPayments.pre_checkout`).
- `successful_payment` — зачёт тем же путём, что у СБП: «✅ Оплачено» пришлёт зачёт.
  Оплата не подошла к заказу — звёзды вернулись, клиенту об этом сообщение.

Правила и зачёт — `services.stars`; возврат по «↩️ Вернуть» — `services.refunds`.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy.exc import SQLAlchemyError

from tsproxy.bot.callbacks import MenuCallback, ScreenId, StarsCallback
from tsproxy.bot.chat_typing import TYPING
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.delivery import Invoice
from tsproxy.bot.screens import Screen
from tsproxy.bot.tariffs import (
    ORDER_REFUSALS,
    TariffScreens,
    offered_or_refused,
    refusal_screen,
)
from tsproxy.content.loader import Texts
from tsproxy.domain.stars import STARS_CURRENCY
from tsproxy.services.stars import StarsInvoice, StarsPaid, StarsPayment

logger = logging.getLogger(__name__)

# Оплата звёздами не записана — тревога на каждый платёж
STARS_UNRECORDED_ALERT = "stars_unrecorded"

# Что сказать клиенту, если оплата не подошла к заказу
PAID_NOTICES = {
    StarsPaid.RETURNED: "stars.returned",
    StarsPaid.NOT_RETURNED: "stars.return_failed",
}


def invoice_of(texts: Texts, invoice: StarsInvoice) -> Invoice:
    """Счёт в звёздах из текстов `stars.*`.

    Под кнопкой оплаты — «К тарифам» и «В меню»: иначе из счёта некуда уйти, кроме оплаты.
    """
    title = invoice.tariff.title
    rows = [
        [InlineKeyboardButton(text=texts.get("stars.pay_button", stars=invoice.stars), pay=True)],
        *(
            [
                InlineKeyboardButton(
                    text=texts.get(key), callback_data=MenuCallback(screen=screen).pack()
                )
            ]
            for key, screen in (
                ("buttons.tariffs_back", ScreenId.TARIFFS),
                ("buttons.back", ScreenId.MAIN),
            )
        ),
    ]
    return Invoice(
        title=texts.get("stars.invoice_title", title=title),
        description=texts.get("stars.invoice_description", title=title),
        payload=invoice.payload,
        currency=STARS_CURRENCY,
        label=texts.get("stars.price_label", title=title),
        amount=invoice.stars,
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def create_stars_router() -> Router:
    """Кнопка «⭐ Stars», проверка перед оплатой и сама оплата — только в личном чате.

    Подключается раньше поддержки: иначе сообщение об оплате ушло бы в обращение.
    """
    router = Router(name="stars")
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.callback_query.register(on_stars, StarsCallback.filter(), flags=TYPING)
    router.pre_checkout_query.register(on_pre_checkout)
    router.message.register(
        on_successful_payment, F.chat.type == ChatType.PRIVATE, F.successful_payment
    )
    return router


async def on_stars(callback: CallbackQuery, callback_data: StarsCallback, ctx: BotContext) -> None:
    """«⭐ Stars»: новый заказ звёздами и счёт сообщением ниже."""
    if not await offered_or_refused(callback, callback_data.tariff_id, ctx):
        return
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    try:
        invoice = await ctx.stars.invoice(client, callback_data.tariff_id)
    except ORDER_REFUSALS as error:
        await ctx.delivery.show(callback, await refusal_screen(ctx, client, error))
        return
    await ctx.delivery.show(callback, TariffScreens(ctx.texts).stars_sent(invoice.stars))
    await ctx.delivery.invoice(client.telegram_id, invoice_of(ctx.texts, invoice))


async def on_pre_checkout(query: PreCheckoutQuery, ctx: BotContext) -> None:
    """Telegram спрашивает «можно оплатить?» — ответить за 10 секунд."""
    try:
        refusal = await ctx.stars.pre_checkout(
            query.from_user.id, query.invoice_payload, query.currency, query.total_amount
        )
    except SQLAlchemyError:
        # база не ответила — оплату не пускаем, но клиент не ждёт молча
        await query.answer(ok=False, error_message=ctx.texts.get("stars.refused.error"))
        raise
    if refusal is None:
        await query.answer(ok=True)
        return
    await query.answer(ok=False, error_message=ctx.texts.get(f"stars.refused.{refusal.value}"))


async def on_successful_payment(message: Message, ctx: BotContext) -> None:
    """Оплата звёздами прошла: засчитать (сообщение «✅ Оплачено» пришлёт зачёт)."""
    paid = message.successful_payment
    if message.from_user is None or paid is None:
        return
    telegram_id = message.from_user.id
    try:
        client = await ctx.clients.resolve(profile_of(message.from_user))
        outcome = await ctx.stars.paid(
            StarsPayment(
                telegram_id=client.telegram_id,
                payload=paid.invoice_payload,
                currency=paid.currency,
                total_amount=paid.total_amount,
                charge_id=paid.telegram_payment_charge_id,
            )
        )
    except SQLAlchemyError:
        # Звёзды уже списаны, а Telegram это сообщение второй раз не пришлёт: общая тревога
        # «ошибка бота» без номера платежа тут не годится — администратору нужен номер
        logger.exception(
            "Оплата звёздами %s клиента %s (счёт «%s», %d ⭐) не записана: база не ответила",
            paid.telegram_payment_charge_id,
            telegram_id,
            paid.invoice_payload,
            paid.total_amount,
        )
        await ctx.alerter.alert(
            STARS_UNRECORDED_ALERT,
            source=on_successful_payment,
            glue=f"{STARS_UNRECORDED_ALERT}:{paid.telegram_payment_charge_id}",
            stars=paid.total_amount,
            telegram_id=telegram_id,
            payload=paid.invoice_payload,
            charge_id=paid.telegram_payment_charge_id,
        )
        await ctx.delivery.send(
            telegram_id, _notice(ctx.texts, "stars.unrecorded", paid.total_amount).kept()
        )
        return
    logger.info("Оплата звёздами от %s: %s", client.telegram_id, outcome)
    key = PAID_NOTICES.get(outcome)
    if key is not None:
        await ctx.delivery.send(
            client.telegram_id, _notice(ctx.texts, key, paid.total_amount).kept()
        )


def _notice(texts: Texts, key: str, stars: int) -> Screen:
    """Сообщение о вернувшихся звёздах — с кнопкой «Поддержка»."""
    support = InlineKeyboardButton(
        text=texts.get("buttons.support"),
        callback_data=MenuCallback(screen=ScreenId.SUPPORT).pack(),
    )
    return Screen(texts.get(key, stars=stars), InlineKeyboardMarkup(inline_keyboard=[[support]]))
