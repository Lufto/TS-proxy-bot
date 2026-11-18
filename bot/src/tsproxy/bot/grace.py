"""Сообщения о льготном периоде (задача 11): текст из `content/client/` и кнопка «Продлить».

Когда и какое сообщение слать, решает `services/grace.py`; здесь — только сборка экрана
и отправка через `ClientDelivery` (он же ставит пометку «клиент заблокировал бота»).
"""

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import Screen
from tsproxy.content.dates import client_time
from tsproxy.content.loader import Texts
from tsproxy.domain.grace import GraceStage

GRACE_TEXTS: dict[GraceStage, str] = {
    GraceStage.STARTED: "grace.started",
    GraceStage.REMINDER: "grace.reminder",
    GraceStage.LAST_DAY: "grace.last_day",
    GraceStage.ENDED: "grace.ended",
}


def grace_screen(texts: Texts, stage: GraceStage, access_until: datetime) -> Screen:
    """Сообщение о льготном периоде с кнопкой «Продлить» (ведёт к тарифам)."""
    renew = InlineKeyboardButton(
        text=texts.get("buttons.renew"),
        callback_data=MenuCallback(screen=ScreenId.TARIFFS).pack(),
    )
    return Screen(
        texts.get(GRACE_TEXTS[stage], until=client_time(access_until)),
        InlineKeyboardMarkup(inline_keyboard=[[renew]]),
    )


class TelegramGraceMessenger:
    """Отправка сообщений о льготном периоде в Telegram. Реализует `GraceMessenger`."""

    def __init__(self, delivery: ClientDelivery, texts: Texts) -> None:
        self._delivery = delivery
        self._texts = texts

    async def send_grace_notice(
        self, telegram_id: int, stage: GraceStage, access_until: datetime
    ) -> bool:
        screen = grace_screen(self._texts, stage, access_until)
        return await self._delivery.send(telegram_id, screen) is not None
