"""Уведомления клиенту по инициативе бота (задача 10): напоминания, продления пробного,
прощание, пробный трафик. Текст и кнопки — из `content/client/notices.yaml` (группа `notices`).

Приходят новым сообщением. Кнопка «Продлить» или «Тарифы» открывает тарифы прямо
в этом сообщении, как любая кнопка меню.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import BUTTON_STYLES, Screen
from tsproxy.content.dates import client_time
from tsproxy.content.loader import Texts
from tsproxy.domain.constants import TRIAL_EXTENSION_DAYS
from tsproxy.domain.profile import format_gigabytes, plural_form
from tsproxy.domain.reminders import AccessPeriod, ReminderKind, TrialTrafficNotice
from tsproxy.services.reminders import (
    ClientNotice,
    ExpiryReminder,
    TrialExtended,
    TrialFarewell,
    TrialTraffic,
)

# (срок, когда) → текст напоминания
REMINDER_TEXTS: dict[tuple[AccessPeriod, ReminderKind], str] = {
    (AccessPeriod.TRIAL, ReminderKind.DAY_BEFORE): "notices.trial_day_before",
    (AccessPeriod.TRIAL, ReminderKind.HOUR_BEFORE): "notices.trial_hour_before",
    (AccessPeriod.PAID, ReminderKind.DAY_BEFORE): "notices.paid_day_before",
    (AccessPeriod.PAID, ReminderKind.HOUR_BEFORE): "notices.paid_hour_before",
}
# Пробному — «Тарифы» (продлевать нечего), оплаченному — «Продлить подписку»
REMINDER_BUTTONS: dict[AccessPeriod, str] = {
    AccessPeriod.TRIAL: "buttons.tariffs",
    AccessPeriod.PAID: "buttons.renew",
}
TRAFFIC_TEXTS: dict[TrialTrafficNotice, str] = {
    TrialTrafficNotice.WARNING: "notices.trial_traffic_warning",
    TrialTrafficNotice.USED_UP: "notices.trial_traffic_used_up",
}


class NoticeScreens:
    """Собирает уведомления из текстов."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _button(self, key: str, screen: ScreenId) -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(
                text=self._texts.get(key),
                callback_data=MenuCallback(screen=screen).pack(),
                style=BUTTON_STYLES.get(key),
            )
        ]

    def _screen(self, text: str, *rows: list[InlineKeyboardButton]) -> Screen:
        keyboard = [*rows, self._button("buttons.back", ScreenId.MAIN)]
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=keyboard))

    def notice(self, notice: ClientNotice) -> Screen:
        match notice:
            case ExpiryReminder(reminder):
                text = self._texts.get(
                    REMINDER_TEXTS[(reminder.period, reminder.kind)],
                    expire_at=client_time(reminder.ends_at),
                )
                return self._screen(
                    text, self._button(REMINDER_BUTTONS[reminder.period], ScreenId.TARIFFS)
                )
            case TrialExtended(expire_at):
                days = plural_form(TRIAL_EXTENSION_DAYS).value
                text = self._texts.get(
                    "notices.trial_extended",
                    days=self._texts.get(f"units.days_{days}", n=TRIAL_EXTENSION_DAYS),
                    expire_at=client_time(expire_at),
                )
                return self._screen(
                    text,
                    self._button("buttons.connect", ScreenId.CONNECT),
                    self._button("buttons.instructions", ScreenId.INSTRUCTIONS),
                )
            case TrialFarewell():
                return self._screen(
                    self._texts.get("notices.trial_farewell"),
                    self._button("buttons.tariffs", ScreenId.TARIFFS),
                )
            case TrialTraffic(kind, used_bytes, limit_bytes):
                text = self._texts.get(
                    TRAFFIC_TEXTS[kind],
                    used=format_gigabytes(used_bytes),
                    limit=format_gigabytes(limit_bytes),
                )
                return self._screen(text, self._button("buttons.tariffs", ScreenId.TARIFFS))
        raise ValueError(f"Неизвестное уведомление: {notice!r}")


class TelegramNotifier:
    """`ClientNotifier` для сервисов: уведомление — новым сообщением клиенту.

    Клиент заблокировал бота — `ClientDelivery` ставит пометку, дальше ему не пишем.
    Остальные ошибки Telegram — наверх: проверка аккаунта упадёт и попадёт в лог и тревогу.
    """

    def __init__(self, screens: NoticeScreens, delivery: ClientDelivery) -> None:
        self._screens = screens
        self._delivery = delivery

    async def notify(self, telegram_id: int, notice: ClientNotice) -> None:
        await self._delivery.send(telegram_id, self._screens.notice(notice))
