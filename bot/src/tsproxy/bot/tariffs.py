"""«💳 Тарифы», `/buy` и экран оплаты (задача 12).

Кнопка тарифа несёт только его id. Цену, доступность тарифа и «та же ссылка
или новая» решает `OrderService`; клиент — всегда `from_user.id` нажавшего.

Задача 14: на экране оплаты — «🔄 Проверить оплату» (бот сам спрашивает платёжку).
«Купить» сначала ищет оплату прошлых заказов: нашлась — она засчитана, вместо ссылки —
«Мы нашли вашу оплату», а «✅ Оплачено» приходит отдельным сообщением ниже.

Задача 15: платёжка отказала создать платёж (например, потребовала email клиента) — клиенту
«платёжный сервис не отвечает», администраторам — тревога с текстом отказа.

Оплата выключена (`PAYMENTS_MODE=off`, задача 29) — вместо кнопок тарифов их список текстом
и «Оплата скоро появится» (+ «🎁 Попробовать бесплатно», если пробный доступен). Старая
кнопка тарифа показывает тот же экран: заказ не создаётся, к платёжке запросов нет.

Задача 33: тариф в списке открывает **экран тарифа** с двумя способами оплаты — «💳 СБП»
(ссылка, как раньше) и «⭐ Stars» (счёт в звёздах, `bot.stars`). Старые кнопки тарифов
ведут сразу в СБП.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import SQLAlchemyError

from tsproxy.bot.callbacks import (
    MenuCallback,
    PaymentCheckCallback,
    ScreenId,
    StarsCallback,
    TariffCallback,
    TariffScreenCallback,
)
from tsproxy.bot.chat_typing import TYPING
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.screens import Screen
from tsproxy.content.loader import Tariff, Texts
from tsproxy.db.models import Client
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.integrations.payments_types import PaymentRejected, PaymentUnavailable
from tsproxy.services.orders import (
    Checkout,
    EarlierPaymentFound,
    PaymentsDisabled,
    TariffOffer,
    TariffUnavailable,
    TooManyPayments,
)
from tsproxy.services.payment_checks import CheckOutcome

logger = logging.getLogger(__name__)

BUY_COMMAND = "buy"
# Ключ склейки тревоги «платёжка не отвечает» — общий для всего бота
PAYMENTS_UNAVAILABLE_ALERT = "payments_unavailable"
# Платёжка отказала создать платёж — одна склеенная тревога с текстом отказа (задача 15)
PAYMENT_CREATE_REJECTED_ALERT = "payment_create_rejected"
PAYMENT_CHECK_BUTTON_ALERT = "payment_check_button_unavailable"
PARAGRAPH = "\n\n"
# Ответ на «🔄 Проверить оплату» — текст экрана и нужна ли кнопка «проверить ещё раз»
CHECK_RESULTS: dict[CheckOutcome, tuple[str, bool]] = {
    CheckOutcome.CREDITED: ("payment.found", False),
    CheckOutcome.ALREADY_CREDITED: ("payment.check_already", False),
    CheckOutcome.NOT_PAID: ("payment.check_not_paid", True),
    CheckOutcome.CANCELED: ("payment.check_canceled", False),
    CheckOutcome.NOT_FOUND: ("payment.check_not_found", False),
    CheckOutcome.REJECTED: ("payment.check_problem", False),
    CheckOutcome.STATUS_CHANGED: ("payment.check_problem", False),
}
# Почему заказ не создан — общее для СБП и звёзд (задача 33)
ORDER_REFUSALS = (
    PaymentsDisabled,
    TariffUnavailable,
    EarlierPaymentFound,
    TooManyPayments,
    PaymentUnavailable,
    PaymentRejected,
)


class TariffScreens:
    """Экраны тарифов и оплаты из `content/client/`."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _menu_button(self, key: str, screen: ScreenId) -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(
                text=self._texts.get(key), callback_data=MenuCallback(screen=screen).pack()
            )
        ]

    def _offer_text(self, offer: TariffOffer) -> str:
        tariff = offer.tariff
        price = format_rubles(tariff.price)
        if offer.discount_percent > 0:
            return self._texts.get(
                "tariffs.button_discount",
                title=tariff.title,
                price=price,
                discount=offer.discount_percent,
            )
        return self._texts.get("tariffs.button", title=tariff.title, price=price)

    def tariffs(self, offers: tuple[TariffOffer, ...]) -> Screen:
        """Список тарифов: по кнопке на тариф."""
        rows = [
            [
                InlineKeyboardButton(
                    text=self._offer_text(offer),
                    callback_data=TariffScreenCallback(tariff_id=offer.tariff.id).pack(),
                )
            ]
            for offer in offers
        ]
        rows.append(self._menu_button("buttons.back", ScreenId.MAIN))
        return Screen(self._texts.get("tariffs.title"), InlineKeyboardMarkup(inline_keyboard=rows))

    def payments_off(self, offers: tuple[TariffOffer, ...], *, trial_available: bool) -> Screen:
        """Оплата выключена: тарифы с ценами текстом, без кнопок оплаты (задача 29)."""
        prices = "\n".join(self._offer_text(offer) for offer in offers)
        note = self._texts.get("tariffs.off_trial" if trial_available else "tariffs.off_later")
        rows = [self._menu_button("buttons.trial", ScreenId.TRIAL)] if trial_available else []
        rows.append(self._menu_button("buttons.back", ScreenId.MAIN))
        return Screen(
            PARAGRAPH.join([self._texts.get("tariffs.off_title"), prices, note]),
            InlineKeyboardMarkup(inline_keyboard=rows),
        )

    def tariff(self, tariff: Tariff, stars: int) -> Screen:
        """Экран тарифа: «💳 СБП» и «⭐ Stars» (задача 33)."""
        price = format_rubles(tariff.price)
        rows = [
            [
                InlineKeyboardButton(
                    text=self._texts.get("buttons.pay_sbp", price=price),
                    callback_data=TariffCallback(tariff_id=tariff.id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=self._texts.get("buttons.pay_stars", stars=stars),
                    callback_data=StarsCallback(tariff_id=tariff.id).pack(),
                )
            ],
        ]
        text = self._texts.get("tariffs.screen", title=tariff.title, price=price, stars=stars)
        rows.append(self._menu_button("buttons.tariffs_back", ScreenId.TARIFFS))
        rows.append(self._menu_button("buttons.back", ScreenId.MAIN))
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def stars_sent(self, stars: int) -> Screen:
        """Счёт в звёздах отправлен сообщением ниже (задача 33)."""
        rows = [
            self._menu_button("buttons.tariffs_back", ScreenId.TARIFFS),
            self._menu_button("buttons.back", ScreenId.MAIN),
        ]
        return Screen(
            self._texts.get("stars.invoice_sent", stars=stars),
            InlineKeyboardMarkup(inline_keyboard=rows),
        )

    def payment(self, checkout: Checkout) -> Screen:
        """Ссылка на оплату, до какого времени она действует и что будет после оплаты."""
        price = format_rubles(checkout.tariff.price)
        text = self._texts.get(
            "payment.screen",
            title=checkout.tariff.title,
            price=price,
            expires_at=format_moscow(checkout.expires_at),
        )
        rows = [
            [
                InlineKeyboardButton(
                    text=self._texts.get("buttons.pay", price=price), url=checkout.payment_url
                )
            ],
            self._check_button(checkout.order_id),
            self._menu_button("buttons.tariffs_back", ScreenId.TARIFFS),
            self._menu_button("buttons.back", ScreenId.MAIN),
        ]
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def notice(self, key: str, *, check_order_id: int | None = None) -> Screen:
        """Сообщение вместо ссылки (`payment.too_many`, `payment.unavailable`, `payment.found`,
        ответы на «Проверить оплату»). `check_order_id` — с кнопкой «проверить ещё раз»."""
        rows = [] if check_order_id is None else [self._check_button(check_order_id)]
        rows.append(self._menu_button("buttons.tariffs_back", ScreenId.TARIFFS))
        rows.append(self._menu_button("buttons.back", ScreenId.MAIN))
        return Screen(self._texts.get(key), InlineKeyboardMarkup(inline_keyboard=rows))

    def check_result(self, outcome: CheckOutcome, order_id: int) -> Screen:
        """Ответ на «🔄 Проверить оплату»."""
        key, check_again = CHECK_RESULTS[outcome]
        return self.notice(key, check_order_id=order_id if check_again else None)

    def _check_button(self, order_id: int) -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(
                text=self._texts.get("buttons.check_payment"),
                callback_data=PaymentCheckCallback(order_id=order_id).pack(),
            )
        ]


def create_tariffs_router() -> Router:
    router = Router(name="tariffs")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_buy_command, Command(BUY_COMMAND))
    router.callback_query.register(on_tariffs, MenuCallback.filter(F.screen == ScreenId.TARIFFS))
    router.callback_query.register(on_tariff_screen, TariffScreenCallback.filter())
    router.callback_query.register(on_tariff, TariffCallback.filter(), flags=TYPING)
    router.callback_query.register(on_payment_check, PaymentCheckCallback.filter(), flags=TYPING)
    return router


async def _tariffs_screen(ctx: BotContext, client: Client) -> Screen:
    """Тарифы с кнопками оплаты — или, если оплата выключена, списком (задача 29)."""
    screens = TariffScreens(ctx.texts)
    offers = ctx.orders.offers()
    if ctx.orders.payments_enabled:
        return screens.tariffs(offers)
    return screens.payments_off(offers, trial_available=await ctx.clients.trial_offered(client))


async def on_buy_command(message: Message, ctx: BotContext) -> None:
    """`/buy` — экран тарифов новым сообщением."""
    if message.from_user is None:
        return
    client = await ctx.clients.resolve(profile_of(message.from_user))
    await ctx.delivery.send(message.from_user.id, await _tariffs_screen(ctx, client))


async def on_tariffs(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    await ctx.delivery.show(callback, await _tariffs_screen(ctx, client))


async def offered_or_refused(callback: CallbackQuery, tariff_id: str, ctx: BotContext) -> bool:
    """Тариф из кнопки есть на витрине нажавшего. Нет — сказать и показать тарифы заново.

    Если есть — на нажатие ещё не ответили: обработчик отвечает сам.
    """
    telegram_id = callback.from_user.id
    if tariff_id in {offer.tariff.id for offer in ctx.orders.offers()}:
        return True
    # старая кнопка (тариф убрали из файла) или подменённая
    logger.info("Клиент %s: тариф %r недоступен", telegram_id, tariff_id)
    await callback.answer(ctx.texts.get("tariffs.not_available"))
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    await ctx.delivery.show(callback, await _tariffs_screen(ctx, client))
    return False


async def refusal_screen(ctx: BotContext, client: Client, error: Exception) -> Screen:
    """Экран вместо оплаты, когда заказ не создан (`ORDER_REFUSALS`) — СБП и звёзды."""
    screens = TariffScreens(ctx.texts)
    telegram_id = client.telegram_id
    match error:
        case PaymentsDisabled() | TariffUnavailable():
            # старая кнопка тарифа, а оплата выключена (задача 29) или тариф недоступен
            logger.info("Клиент %s: заказ не создан: %s", telegram_id, error)
            return await _tariffs_screen(ctx, client)
        case EarlierPaymentFound(order_id=order_id):
            # оплата прошлого заказа засчитана, «✅ Оплачено» ушло отдельным сообщением
            logger.info("Клиент %s: нашлась оплата заказа %s", telegram_id, order_id)
            return screens.notice("payment.found")
        case TooManyPayments():
            return screens.notice("payment.too_many")
        case PaymentUnavailable():
            logger.warning("Платёжка не ответила для клиента %s: %s", telegram_id, error)
            await ctx.alerter.alert(PAYMENTS_UNAVAILABLE_ALERT, source=refusal_screen, error=error)
            return screens.notice("payment.unavailable")
        case PaymentRejected():
            logger.error("Платёжка отказала создать платёж клиенту %s: %s", telegram_id, error)
            await ctx.alerter.alert(
                PAYMENT_CREATE_REJECTED_ALERT, source=refusal_screen, error=error
            )
            return screens.notice("payment.unavailable")
    raise TypeError(f"неожиданная ошибка заказа: {error!r}")


async def on_tariff_screen(
    callback: CallbackQuery, callback_data: TariffScreenCallback, ctx: BotContext
) -> None:
    """Нажат тариф в списке: экран тарифа со способами оплаты (задача 33)."""
    if not await offered_or_refused(callback, callback_data.tariff_id, ctx):
        return
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    tariff = next(
        offer.tariff for offer in ctx.orders.offers() if offer.tariff.id == callback_data.tariff_id
    )
    if not ctx.orders.payments_enabled:
        # оплата выключена (задача 29): тот же экран, что у списка тарифов
        await ctx.delivery.show(callback, await _tariffs_screen(ctx, client))
        return
    screen = TariffScreens(ctx.texts).tariff(tariff, ctx.stars.price(tariff))
    await ctx.delivery.show(callback, screen)


async def on_tariff(
    callback: CallbackQuery, callback_data: TariffCallback, ctx: BotContext
) -> None:
    """«💳 СБП» (или старая кнопка тарифа): ссылка на оплату (та же, если она ещё жива)."""
    if not await offered_or_refused(callback, callback_data.tariff_id, ctx):
        return
    # Отвечаем на нажатие сразу: платёжка может думать несколько секунд
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    try:
        checkout = await ctx.orders.checkout(client, callback_data.tariff_id)
    except ORDER_REFUSALS as error:
        screen = await refusal_screen(ctx, client, error)
    else:
        screen = TariffScreens(ctx.texts).payment(checkout)
    await ctx.delivery.show(callback, screen)


async def on_payment_check(
    callback: CallbackQuery, callback_data: PaymentCheckCallback, ctx: BotContext
) -> None:
    """«🔄 Проверить оплату»: бот спрашивает платёжку о заказе нажавшего (задача 14)."""
    # Отвечаем на нажатие сразу: платёжка может думать несколько секунд
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    screens = TariffScreens(ctx.texts)
    order_id = callback_data.order_id
    try:
        outcome = await ctx.payment_checks.check_order(client.id, order_id)
    except PaymentsDisabled:
        # старая кнопка, а оплата выключена (задача 29): проверить не у кого
        screen = await _tariffs_screen(ctx, client)
    except PaymentUnavailable as error:
        logger.warning("Платёжка не ответила на проверку заказа %s: %s", order_id, error)
        await ctx.alerter.alert(
            PAYMENT_CHECK_BUTTON_ALERT,
            source=on_payment_check,
            error=error,
            glue=PAYMENTS_UNAVAILABLE_ALERT,
            order_id=order_id,
        )
        screen = screens.notice("payment.check_unavailable", check_order_id=order_id)
    except SQLAlchemyError:
        # оплата есть, а база её не записала: тревогу с номером платежа уже поднял зачёт,
        # повторы идут фоновой проверкой; не вышло — доступ выдаёт администратор
        logger.exception("Заказ %s: оплата не записалась при проверке клиентом", order_id)
        screen = screens.notice("payment.not_recorded")
    else:
        logger.info("Клиент %s проверил заказ %s: %s", client.telegram_id, order_id, outcome)
        screen = screens.check_result(outcome, order_id)
    await ctx.delivery.show(callback, screen)
