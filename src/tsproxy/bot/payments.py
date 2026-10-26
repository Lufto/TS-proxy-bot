"""Сообщения клиенту об оплате (задача 13): «✅ Оплачено, подписка до …» и «доступ подключается».

Задача 14: «доступ задерживается» (не выдан за 30 минут) и «получили две оплаты».

Реализует `services.crediting.PaymentMessages`. Оплата к этому моменту уже засчитана,
поэтому сбой Telegram не бросается дальше, а пишется в лог: подписку клиент всё равно
увидит в «🚀 Подключиться».
"""

import logging
from datetime import datetime

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import Screen, Screens
from tsproxy.content.loader import Texts
from tsproxy.domain.constants import PAID_DEVICE_LIMIT
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.services.crediting import GrantedAccess

logger = logging.getLogger(__name__)


class PaymentScreens:
    """Экраны «оплачено» из `content/texts.yaml`."""

    def __init__(self, texts: Texts, screens: Screens) -> None:
        self._texts = texts
        self._screens = screens

    def paid(self, granted: GrantedAccess) -> Screen:
        """Оплачено: срок, ссылка подписки; при сумме не по тарифу — с извинением."""
        account = granted.account
        values: dict[str, object] = {
            "expire_at": format_moscow(account.expire_at),
            "device_limit": PAID_DEVICE_LIMIT,
        }
        if granted.prorated:
            text = self._texts.get(
                "payment.paid_prorated",
                amount=format_rubles(granted.paid_amount),
                price=format_rubles(granted.price),
                **values,
            )
        else:
            text = self._texts.get("payment.paid", **values)
        return self._screens.access_granted(text, account)

    def pending(self) -> Screen:
        """Оплата получена, доступ подключается — бот сам пришлёт «✅ Оплачено»."""
        return self._with_back(self._texts.get("payment.pending"))

    def delayed(self) -> Screen:
        """Доступ не выдан за 30 минут: «задерживается, разбираемся» (задача 14)."""
        return self._with_back(self._texts.get("payment.delayed"))

    def double(self, refund_until: datetime) -> Screen:
        """Две оплаты подряд: дни сложены, вторую можно вернуть до `refund_until`."""
        return self._with_back(self._texts.get("payment.double", until=format_moscow(refund_until)))

    def _with_back(self, text: str) -> Screen:
        back = InlineKeyboardButton(
            text=self._texts.get("buttons.back"),
            callback_data=MenuCallback(screen=ScreenId.MAIN).pack(),
        )
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=[[back]]))


class TelegramPaymentMessages:
    """Отправка сообщений об оплате новым сообщением в чат клиента."""

    def __init__(self, delivery: ClientDelivery, screens: PaymentScreens) -> None:
        self._delivery = delivery
        self._screens = screens

    async def access_granted(self, granted: GrantedAccess) -> None:
        await self._send(granted.telegram_id, self._screens.paid(granted))

    async def access_pending(self, telegram_id: int) -> None:
        await self._send(telegram_id, self._screens.pending())

    async def access_delayed(self, telegram_id: int) -> None:
        await self._send(telegram_id, self._screens.delayed())

    async def possible_double_payment(self, telegram_id: int, refund_until: datetime) -> None:
        await self._send(telegram_id, self._screens.double(refund_until))

    async def _send(self, telegram_id: int, screen: Screen) -> None:
        try:
            await self._delivery.send(telegram_id, screen)
        # Оплата засчитана и доступ выдан (или выдаётся): ссылка есть в «🚀 Подключиться».
        # Сбой Telegram не должен ронять зачёт или фоновую задачу повторов.
        except TelegramAPIError:
            logger.exception("Не удалось прислать сообщение об оплате клиенту %s", telegram_id)
