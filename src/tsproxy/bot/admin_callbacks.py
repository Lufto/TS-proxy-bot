"""Данные кнопок админки (задача 18).

Кнопки админки несут только «что сделать и с каким клиентом» (Telegram ID и параметры).
Права они не дают: каждый обработчик заново проверяет, что нажал администратор и что
кнопка в группе администраторов (`AdminFilter`), — кнопку может прислать кто угодно.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from aiogram.filters.callback_data import CallbackData
from pydantic import Field

from tsproxy.domain.admin import MAX_TELEGRAM_ID
from tsproxy.domain.constants import MAX_MANUAL_GRANT_DAYS
from tsproxy.services.profile import DEVICE_REF_LENGTH

# В кнопке подтверждения выдачи: «у клиента не было прокси-аккаунта»
NO_ACCOUNT_STAMP = 0

TelegramId = Annotated[int, Field(gt=0, le=MAX_TELEGRAM_ID)]


class AdminAction(StrEnum):
    """Действия над клиентом из карточки. `*_ask` — вопрос с подтверждением."""

    CARD = "card"
    DEVICES = "devices"
    RESET_TRIAL_ASK = "trial_ask"
    RESET_TRIAL = "trial"
    REVOKE_ASK = "revoke_ask"
    REVOKE = "revoke"
    CANCEL = "cancel"


class AdminClientCallback(CallbackData, prefix="adm"):
    action: AdminAction
    telegram_id: TelegramId


class AdminExtendCallback(CallbackData, prefix="admx"):
    """«➕ N дн.»: `confirm=False` — показать предпросмотр, `True` — выдать.

    `stamp` — срок, который видел администратор в предпросмотре (секунды Unix,
    `NO_ACCOUNT_STAMP` — аккаунта не было). Если срок уже другой, выдачи не будет.
    """

    telegram_id: TelegramId
    days: Annotated[int, Field(ge=1, le=MAX_MANUAL_GRANT_DAYS)]
    confirm: bool = False
    stamp: Annotated[int, Field(ge=0)] = NO_ACCOUNT_STAMP


class AdminDeviceCallback(CallbackData, prefix="admd"):
    """«🗑 N» в списке устройств клиента: ищется только среди устройств этого клиента."""

    telegram_id: TelegramId
    ref: Annotated[str, Field(pattern=rf"^[0-9a-f]{{{DEVICE_REF_LENGTH}}}$")]


def expire_stamp(expire_at: datetime | None) -> int:
    """Срок для кнопки подтверждения — с точностью до секунды."""
    return NO_ACCOUNT_STAMP if expire_at is None else int(expire_at.timestamp())


def stamp_expire(stamp: int) -> datetime | None:
    """Обратно из кнопки: срок или None (аккаунта не было)."""
    return None if stamp == NO_ACCOUNT_STAMP else datetime.fromtimestamp(stamp, UTC)
