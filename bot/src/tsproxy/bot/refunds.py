"""Возвраты у клиента (задача 21, решение владельца 2026-10-30).

Кнопки «вернуть деньги» у клиента нет — возвраты через поддержку (`bot.admin_refunds`), в том
числе случайной второй оплаты (владелец, 2026-11-10). Здесь — `TelegramRefundMessages`:
сообщение клиенту о законченном возврате.
"""

import logging
from datetime import datetime
from decimal import Decimal

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import Screen
from tsproxy.content.amounts import paid_text
from tsproxy.content.loader import Texts
from tsproxy.domain.dates import format_moscow

logger = logging.getLogger(__name__)


class RefundScreens:
    """Экраны возврата у клиента из `content/client/`."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _button(self, key: str, screen: ScreenId) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self._texts.get(key), callback_data=MenuCallback(screen=screen).pack()
        )

    def _screen(self, text: str, *rows: list[InlineKeyboardButton]) -> Screen:
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=list(rows)))

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
