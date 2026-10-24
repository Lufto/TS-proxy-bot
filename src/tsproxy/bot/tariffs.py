"""«💳 Тарифы», `/buy` и экран оплаты (задача 12).

Кнопка тарифа несёт только его id. Цену, доступность скрытого тарифа и «та же ссылка
или новая» решает `OrderService`; клиент — всегда `from_user.id` нажавшего.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tsproxy.bot.callbacks import MenuCallback, ScreenId, TariffCallback
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.screens import Screen
from tsproxy.content.loader import Texts
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.integrations.payments_types import PaymentUnavailable
from tsproxy.services.orders import Checkout, TariffOffer, TariffUnavailable, TooManyPayments

logger = logging.getLogger(__name__)

BUY_COMMAND = "buy"
# Ключ склейки тревоги «платёжка не отвечает» — общий для всего бота
PAYMENTS_UNAVAILABLE_ALERT = "payments_unavailable"


class TariffScreens:
    """Экраны тарифов и оплаты из `content/texts.yaml`."""

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
        if tariff.hidden:
            return self._texts.get("tariffs.button_hidden", title=tariff.title, price=price)
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
                    callback_data=TariffCallback(tariff_id=offer.tariff.id).pack(),
                )
            ]
            for offer in offers
        ]
        rows.append(self._menu_button("buttons.back", ScreenId.MAIN))
        return Screen(self._texts.get("tariffs.title"), InlineKeyboardMarkup(inline_keyboard=rows))

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
            self._menu_button("buttons.tariffs_back", ScreenId.TARIFFS),
            self._menu_button("buttons.back", ScreenId.MAIN),
        ]
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def notice(self, key: str) -> Screen:
        """Оплатить сейчас не получится (`payment.too_many`, `payment.unavailable`)."""
        rows = [
            self._menu_button("buttons.tariffs_back", ScreenId.TARIFFS),
            self._menu_button("buttons.back", ScreenId.MAIN),
        ]
        return Screen(self._texts.get(key), InlineKeyboardMarkup(inline_keyboard=rows))


def create_tariffs_router() -> Router:
    router = Router(name="tariffs")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_buy_command, Command(BUY_COMMAND))
    router.callback_query.register(on_tariffs, MenuCallback.filter(F.screen == ScreenId.TARIFFS))
    router.callback_query.register(on_tariff, TariffCallback.filter())
    return router


def _tariffs_screen(ctx: BotContext, telegram_id: int) -> Screen:
    return TariffScreens(ctx.texts).tariffs(ctx.orders.offers(telegram_id))


async def on_buy_command(message: Message, ctx: BotContext) -> None:
    """`/buy` — экран тарифов новым сообщением."""
    if message.from_user is None:
        return
    await ctx.clients.resolve(profile_of(message.from_user))
    await ctx.delivery.send(message.from_user.id, _tariffs_screen(ctx, message.from_user.id))


async def on_tariffs(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    await ctx.clients.resolve(profile_of(callback.from_user))
    await ctx.delivery.show(callback, _tariffs_screen(ctx, callback.from_user.id))


async def on_tariff(
    callback: CallbackQuery, callback_data: TariffCallback, ctx: BotContext
) -> None:
    """Нажат тариф: ссылка на оплату (та же, если она ещё жива)."""
    telegram_id = callback.from_user.id
    offered = {offer.tariff.id for offer in ctx.orders.offers(telegram_id)}
    if callback_data.tariff_id not in offered:
        # старая кнопка (тариф убрали из файла) или подменённая (скрытый тариф)
        logger.info("Клиент %s: тариф %r недоступен", telegram_id, callback_data.tariff_id)
        await callback.answer(ctx.texts.get("tariffs.not_available"))
        await ctx.delivery.show(callback, _tariffs_screen(ctx, telegram_id))
        return
    # Отвечаем на нажатие сразу: платёжка может думать несколько секунд
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    screens = TariffScreens(ctx.texts)
    try:
        checkout = await ctx.orders.checkout(client, callback_data.tariff_id)
    except TariffUnavailable:
        logger.info("Клиент %s: тариф %r недоступен", telegram_id, callback_data.tariff_id)
        screen = _tariffs_screen(ctx, telegram_id)
    except TooManyPayments:
        screen = screens.notice("payment.too_many")
    except PaymentUnavailable as error:
        logger.warning("Платёжка не ответила для клиента %s: %s", telegram_id, error)
        await ctx.alerter.raise_alert(
            PAYMENTS_UNAVAILABLE_ALERT, ctx.admin_texts.get("alerts.payments_unavailable")
        )
        screen = screens.notice("payment.unavailable")
    else:
        screen = screens.payment(checkout)
    await ctx.delivery.show(callback, screen)
