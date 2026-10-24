"""Общий язык для работы с панелью Remnawave: модели, ошибки, интерфейс и защита чужих аккаунтов.

Этим модулем пользуются и настоящий клиент (`remnawave.py`), и подделка (`fakes.py`),
поэтому правила защиты в них одни и те же.
"""

import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from tsproxy.config import RunMode

# Прокси-аккаунты, которые бот создаёт сам. Всё остальное в панели — нынешние клиенты.
PROD_USERNAME_PREFIX = "tg_"
DEV_USERNAME_PREFIX = "dev_"
# В разработке бот трогает только аккаунты из этого внутреннего squad'а
DEV_SQUAD_NAME = "test"

PROD_USERNAME_RE = re.compile(r"^tg_\d+$")
DEV_USERNAME_RE = re.compile(r"^dev_\d+$")

UNLIMITED_TRAFFIC = 0  # в Remnawave лимит трафика 0 значит «без лимита»


class AccountStatus(StrEnum):
    """Статус прокси-аккаунта в Remnawave."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    LIMITED = "LIMITED"  # трафик исчерпан, доступа нет
    EXPIRED = "EXPIRED"


class ProxyAccount(BaseModel):
    """Прокси-аккаунт клиента — только те поля панели, которыми пользуется бот."""

    model_config = ConfigDict(frozen=True)

    id: int  # числовой id в панели (в Remnawave 3.x вместо uuid)
    username: str
    telegram_id: int | None
    status: AccountStatus
    expire_at: AwareDatetime
    traffic_used_bytes: int
    traffic_limit_bytes: int  # 0 — без лимита
    device_limit: int | None  # None — действует общий лимит панели
    first_connected_at: AwareDatetime | None
    squad_names: frozenset[str]
    # Ссылка подписки даёт доступ к прокси — не показываем её в repr и логах
    subscription_url: str = Field(repr=False)

    @property
    def ever_connected(self) -> bool:
        """Подключался ли клиент хоть раз (Remnawave знает первое подключение)."""
        return self.first_connected_at is not None


class Device(BaseModel):
    """Устройство клиента (HWID), подключённое к прокси-аккаунту."""

    model_config = ConfigDict(frozen=True)

    hwid: str
    platform: str | None
    os_version: str | None
    device_model: str | None
    created_at: AwareDatetime


class Node(BaseModel):
    """Нода (сервер прокси) и её состояние связи с панелью."""

    model_config = ConfigDict(frozen=True)

    uuid: str
    name: str
    is_connected: bool
    is_disabled: bool
    last_status_change: AwareDatetime | None
    last_status_message: str | None
    users_online: int | None


# --- Ошибки ---


class RemnawaveError(Exception):
    """Общий предок всех ошибок обёртки Remnawave."""


class AccountNotFound(RemnawaveError):
    """Прокси-аккаунта клиента в панели нет."""


class ForeignAccountError(RemnawaveError):
    """Аккаунт не принадлежит боту в текущем режиме — трогать его нельзя."""


class OperationNotAllowed(RemnawaveError):
    """Действие запрещено в текущем режиме (например, удаление аккаунта в боевом)."""


class RemnawaveUnavailable(RemnawaveError):
    """Панель не ответила: сеть, таймаут, 5xx или 429. Повтор позже может помочь."""


class RemnawaveAuthError(RemnawaveError):
    """Панель не приняла токен (401/403). Повтор не поможет — нужен новый токен."""


class RemnawaveRejected(RemnawaveError):
    """Панель отклонила запрос (400 и т.п.): неверные данные. Повтор не поможет."""

    def __init__(self, message: str, *, status_code: int, code: str | None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class RemnawaveResponseError(RemnawaveError):
    """Ответ панели не совпал с ожидаемой формой (обычно — другая версия панели)."""


class UnexpectedPanelVersion(RemnawaveError):
    """Версия панели не та, под которую написан бот. Запускаться нельзя."""


# --- Защита нынешних клиентов ---


class AccountGuard:
    """Решает, какие прокси-аккаунты боту можно трогать.

    Боевой режим: только `tg_<цифры>`. Разработка: только `dev_<цифры>` в squad `test`.
    Проверка стоит в обёртке, а не в вызывающем коде, чтобы ошибка в сервисе
    не задела 28 аккаунтов, заведённых вручную.
    """

    def __init__(self, mode: RunMode) -> None:
        self.mode = mode

    def username_for(self, telegram_id: int) -> str:
        """Имя прокси-аккаунта клиента в панели."""
        if telegram_id <= 0:
            raise ForeignAccountError(f"Неверный Telegram ID: {telegram_id}")
        prefix = PROD_USERNAME_PREFIX if self.mode is RunMode.PROD else DEV_USERNAME_PREFIX
        return f"{prefix}{telegram_id}"

    def is_own(self, username: str, squad_names: Iterable[str]) -> bool:
        if self.mode is RunMode.PROD:
            return PROD_USERNAME_RE.fullmatch(username) is not None
        return DEV_USERNAME_RE.fullmatch(username) is not None and DEV_SQUAD_NAME in set(
            squad_names
        )

    def ensure_own(self, username: str, squad_names: Iterable[str]) -> None:
        """Бросает ForeignAccountError, если аккаунт не наш."""
        if not self.is_own(username, squad_names):
            raise ForeignAccountError(
                f"Аккаунт {username!r} не принадлежит боту в режиме {self.mode.value}"
            )


# --- Интерфейс ---


class RemnawaveClient(Protocol):
    """Всё, что остальной код бота знает о Remnawave. Аккаунт клиента — по Telegram ID."""

    async def check_panel_version(self) -> str:
        """Версия панели; UnexpectedPanelVersion, если основная версия не та."""
        ...

    async def get_account(self, telegram_id: int) -> ProxyAccount:
        """Прокси-аккаунт клиента; AccountNotFound, если его нет."""
        ...

    async def get_or_create_account(
        self,
        telegram_id: int,
        *,
        expire_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
    ) -> ProxyAccount:
        """Создаёт аккаунт; если он уже есть — возвращает существующий без изменений."""
        ...

    async def set_expiry(self, telegram_id: int, expire_at: datetime) -> ProxyAccount:
        """Ставит срок «до X» (абсолютная дата, повтор безопасен)."""
        ...

    async def set_limits(
        self, telegram_id: int, *, device_limit: int, traffic_limit_bytes: int
    ) -> ProxyAccount:
        """Ставит лимит устройств и трафика (0 — без лимита)."""
        ...

    async def enable_account(self, telegram_id: int) -> ProxyAccount: ...

    async def disable_account(self, telegram_id: int) -> ProxyAccount: ...

    async def add_to_squad(self, telegram_id: int, squad_name: str) -> ProxyAccount:
        """Добавляет аккаунт во внутренний squad, сохраняя остальные."""
        ...

    async def list_devices(self, telegram_id: int) -> list[Device]: ...

    async def delete_device(self, telegram_id: int, hwid: str) -> list[Device]:
        """Удаляет одно устройство, возвращает оставшиеся."""
        ...

    async def revoke_subscription(self, telegram_id: int) -> ProxyAccount:
        """Перевыпускает ссылку подписки: старая перестаёт работать."""
        ...

    async def list_accounts(self) -> list[ProxyAccount]:
        """Все аккаунты бота в текущем режиме; чужие отфильтрованы."""
        ...

    async def list_nodes(self) -> list[Node]: ...

    async def delete_account(self, telegram_id: int) -> None:
        """Удаляет аккаунт. Только в режиме разработки: в боевом аккаунты не удаляем."""
        ...
