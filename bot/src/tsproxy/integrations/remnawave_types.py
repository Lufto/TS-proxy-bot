"""Общий язык для работы с панелью Remnawave: модели, ошибки, интерфейс и защита чужих аккаунтов.

Этим модулем пользуются и настоящий клиент (`remnawave.py`), и подделка (`fakes.py`),
поэтому правила защиты в них одни и те же.
"""

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from tsproxy.config import RunMode
from tsproxy.integrations.failures import ServiceError

# Прокси-аккаунты, которые бот создаёт сам. Всё остальное в панели — нынешние клиенты.
PROD_USERNAME_PREFIX = "tg_"
DEV_USERNAME_PREFIX = "dev_"
# В разработке бот трогает только аккаунты из этого внутреннего squad'а
DEV_SQUAD_NAME = "test"

PROD_USERNAME_RE = re.compile(r"^tg_\d+$")
DEV_USERNAME_RE = re.compile(r"^dev_\d+$")

UNLIMITED_TRAFFIC = 0  # в Remnawave лимит трафика 0 значит «без лимита»

# Заголовок ответа подписки, из которого Happ и v2RayTun берут объявление (задача 35).
# Панель хранит имена своих заголовков маленькими буквами
ANNOUNCE_HEADER = "announce"
# Значение заголовка HTTP: видимые символы ASCII и пробел. Русские буквы или перевод строки
# панель отдать не сможет — сломается ответ подписки у всех клиентов
HEADER_VALUE_RE = re.compile(r"[\x20-\x7e]+")


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


class RemnawaveFailure(StrEnum):
    """Виды сбоя Remnawave; значение — ключ текстов `failure.<вид>` в `alerts/remnawave.yaml`."""

    UNAVAILABLE = "remnawave.unavailable"  # вид не уточнён (подделка в тестах, HTTP 0)
    NO_CONNECTION = "remnawave.no_connection"
    TIMEOUT = "remnawave.timeout"
    SERVER_ERROR = "remnawave.server_error"  # 5xx
    RATE_LIMITED = "remnawave.rate_limited"  # 429
    TOKEN_REJECTED = "remnawave.token_rejected"  # noqa: S105 — 401/403, не пароль
    BAD_RESPONSE = "remnawave.bad_response"
    UNEXPECTED_VERSION = "remnawave.unexpected_version"


class RemnawaveError(ServiceError):
    """Общий предок всех ошибок обёртки Remnawave."""


class AccountNotFound(RemnawaveError):
    """Прокси-аккаунта клиента в панели нет."""


class ForeignAccountError(RemnawaveError):
    """Аккаунт не принадлежит боту в текущем режиме — трогать его нельзя."""


class OperationNotAllowed(RemnawaveError):
    """Действие запрещено в текущем режиме (например, удаление аккаунта в боевом)."""


class RemnawaveUnavailable(RemnawaveError):
    """Панель не ответила: сеть, таймаут, 5xx или 429. Повтор позже может помочь."""

    default_kind = RemnawaveFailure.UNAVAILABLE


class RemnawaveAuthError(RemnawaveError):
    """Панель не приняла токен (401/403). Повтор не поможет — нужен новый токен."""

    default_kind = RemnawaveFailure.TOKEN_REJECTED


class RemnawaveRejected(RemnawaveError):
    """Панель отклонила запрос (400 и т.п.): неверные данные. Повтор не поможет."""

    def __init__(self, message: str, *, status_code: int, code: str | None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class RemnawaveResponseError(RemnawaveError):
    """Ответ панели не совпал с ожидаемой формой (обычно — другая версия панели)."""

    default_kind = RemnawaveFailure.BAD_RESPONSE


class UnexpectedPanelVersion(RemnawaveError):
    """Версия панели не та, под которую написан бот. Запускаться нельзя."""

    default_kind = RemnawaveFailure.UNEXPECTED_VERSION


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


# --- Свои заголовки ответа подписки (настройки подписки панели) ---


def check_header_value(value: str) -> str:
    """Вернуть значение как есть или упасть, если его нельзя отдать заголовком HTTP."""
    if HEADER_VALUE_RE.fullmatch(value) is None:
        raise ValueError("Значение заголовка — только латиница, цифры, знаки и пробел")
    return value


def find_header(headers: Mapping[str, str], name: str) -> str | None:
    """Значение заголовка без учёта регистра имени. `None` — такого нет."""
    return next((v for k, v in headers.items() if k.lower() == name), None)


def without_header(headers: Mapping[str, str], name: str) -> dict[str, str]:
    """Те же заголовки без `name` (в любом регистре). Остальные не меняются."""
    return {k: v for k, v in headers.items() if k.lower() != name}


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

    async def reset_traffic(self, telegram_id: int) -> ProxyAccount:
        """Обнуляет израсходованный трафик (повторный пробный после сброса администратором)."""
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

    # Объявление (задача 35) — заголовок `announce` в общих настройках подписки панели.
    # Настройки одни на всю панель: объявление видят все клиенты, и нынешние тоже

    async def get_announce(self) -> str | None:
        """Значение заголовка `announce`, как оно записано в панели. `None` — не задан."""
        ...

    async def set_announce(self, value: str) -> str | None:
        """Ставит `announce`, остальные заголовки не трогает. Возвращает прежнее значение.

        `value` — готовое значение заголовка (ASCII); иначе ValueError до записи в панель.
        """
        ...

    async def clear_announce(self) -> str | None:
        """Убирает `announce`, остальные заголовки не трогает. Снятое значение; `None` — не было."""
        ...
