"""Данные кнопок (`callback_data`) — только через типизированные фабрики aiogram.

Кнопку присылает приложение клиента, и модифицированное приложение пришлёт что угодно
(аудит §4.3). Поэтому в данных кнопок нет ничего, что даёт доступ: только «какой экран
открыть». Чей это клиент, бот всегда определяет по `from_user.id` нажавшего.
Кривые данные отбрасывает pydantic: такой кнопке не подходит ни один обработчик.
"""

from enum import StrEnum
from typing import Annotated

from aiogram.filters.callback_data import CallbackData
from pydantic import Field

from tsproxy.content.loader import QUICK_REPLY_ID_PATTERN
from tsproxy.services.profile import DEVICE_REF_LENGTH


class ScreenId(StrEnum):
    """Экраны меню: нажатие присылает экран новым сообщением (живое меню)."""

    MAIN = "main"
    CONNECT = "connect"  # «Подключиться»
    TRIAL = "trial"  # «Попробовать бесплатно» — задача 07
    TARIFFS = "tariffs"  # «Тарифы» — задача 12
    PROFILE = "profile"  # «Мой профиль» — задача 08
    DEVICES = "devices"  # «Мои устройства» — задача 08
    REVOKE_LINK = "revoke_link"  # «Перевыпустить ссылку»: предупреждение — задача 31
    INSTRUCTIONS = "instructions"
    SUPPORT = "support"  # «Поддержка» — задачи 16–17
    ABOUT = "about"


class Platform(StrEnum):
    """Платформы в «📖 Инструкциях»."""

    IOS = "ios"
    ANDROID = "android"
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"


class MenuCallback(CallbackData, prefix="menu"):
    screen: ScreenId


class InstructionsCallback(CallbackData, prefix="howto"):
    platform: Platform


class DeviceCallback(CallbackData, prefix="device"):
    """«Удалить» устройство. `ref` — короткий хеш HWID (`services.profile.device_ref`).

    Устройство ищется только среди устройств нажавшего, поэтому чужой `ref` ничего не удалит.
    """

    ref: Annotated[str, Field(pattern=rf"^[0-9a-f]{{{DEVICE_REF_LENGTH}}}$")]


class RevokeLinkCallback(CallbackData, prefix="relink"):
    """«Да, перевыпустить» (задача 31). Без данных: чью ссылку — решает `from_user.id`."""


class TariffScreenCallback(CallbackData, prefix="tariff"):
    """Тариф в списке «Тарифы» — экран тарифа с выбором способа оплаты (задача 33)."""

    tariff_id: str


class TariffCallback(CallbackData, prefix="buy"):
    """«💳 СБП» на экране тарифа — ссылка на оплату. Только id тарифа: цену и доступность
    проверяет сервис заказов. Старые кнопки тарифов (до задачи 33) — тоже СБП."""

    tariff_id: str


class StarsCallback(CallbackData, prefix="stars"):
    """«⭐ Stars» на экране тарифа — счёт в звёздах Telegram (задача 33)."""

    tariff_id: str


class PaymentCheckCallback(CallbackData, prefix="paycheck"):
    """«Проверить оплату» (задача 14). Номер заказа — не доступ: заказ ищется
    только среди заказов нажавшего (аудит §4.3, R07)."""

    order_id: int


# --- Поддержка (задача 17) ---


class FaqQuestion(StrEnum):
    """Частые вопросы на экране «Поддержка». Тексты — `faq.<значение>` в `content/client/`.

    Порядок — порядок кнопок. `OTHER` всегда последний: только подсказка «напишите сюда»;
    обращение откроется, когда клиент что-то напишет, — иначе нажавшие и передумавшие
    оставляли бы пустые темы в группе администраторов.
    """

    CONNECT = "connect"
    NOT_WORKING = "not_working"
    DEVICES = "devices"
    RENEW = "renew"
    PAYMENT = "payment"
    OTHER = "other"  # «Другой вопрос»


class FaqCallback(CallbackData, prefix="faq"):
    question: FaqQuestion


class TicketAction(StrEnum):
    """Кнопки в теме обращения. Нажать их может только администратор в группе (`AdminFilter`)."""

    TAKE = "take"  # «🙋 Беру»
    CLOSE = "close"  # «✅ Закрыть»
    QUICK_REPLIES = "quick"  # «💬 Быстрые ответы» — показать заготовки
    HIDE = "hide"  # «✖️ Скрыть» меню заготовок
    EXTEND = "extend"  # «➕ Продлить на N дней» — задача 18
    DEVICES = "devices"  # «📱 Устройства» — задача 18
    REFUND = "refund"  # «↩️ Вернуть» — оплаты клиента за 14 дней, задача 21


class TicketCallback(CallbackData, prefix="ticket"):
    """Кнопка в теме обращения. Номер обращения — не доступ: права проверяет `AdminFilter`."""

    action: TicketAction
    ticket_id: int


class QuickReplyCallback(CallbackData, prefix="qr"):
    """Заготовка ответа. Имя проверяется по `content/admin/` при нажатии."""

    ticket_id: int
    reply: Annotated[str, Field(pattern=rf"^{QUICK_REPLY_ID_PATTERN}$")]
