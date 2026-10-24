"""Настоящий клиент Remnawave поверх `remnawave-api` (панель 3.4.x).

Остальной код бота видит только интерфейс `RemnawaveClient` из `remnawave_types`.
Перед каждым изменением аккаунт перечитывается из панели и проверяется `AccountGuard`:
чужие аккаунты (28 нынешних клиентов) не меняются ни при какой ошибке в сервисах.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx
import pydantic
from remnawave import RemnawaveSDK
from remnawave.exceptions import ApiError
from remnawave.models import (
    CreateUserBodyDto,
    HWIDDeleteRequest,
    RevokeUserSubscriptionBodyDto,
    UpdateUserBodyDto,
    UserResponseDto,
)
from remnawave.models.hwid import HwidDeviceDto
from remnawave.models.nodes import NodeResponseDto

from tsproxy.config import RunMode
from tsproxy.integrations.remnawave_types import (
    DEV_SQUAD_NAME,
    AccountGuard,
    AccountNotFound,
    AccountStatus,
    Device,
    Node,
    OperationNotAllowed,
    ProxyAccount,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveRejected,
    RemnawaveResponseError,
    RemnawaveUnavailable,
    UnexpectedPanelVersion,
)

logger = logging.getLogger(__name__)

# Бот написан под панель 3.x; другая основная версия — отказ запускаться
SUPPORTED_PANEL_MAJOR = 3
TESTED_PANEL_VERSION = "3.4.5"

# Аудит §6.2: таймаут 10 с; повторы — только чтение и операции с абсолютным значением
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 0.5
STREAM_PAGE_SIZE = 500

# Cloudflare перед панелью блокирует стандартный User-Agent Python (задача 03)
USER_AGENT = "TSProxyBot/1.0 (+https://ts-proxy.com)"

# Коды ошибок панели (libs/contract/constants/errors/errors.ts, тег 3.4.5)
USERNAME_EXISTS_CODE = "A019"  # приходит с HTTP 400, а не 409
ALREADY_DISABLED_CODE = "A029"
ALREADY_ENABLED_CODE = "A030"

HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
AUTH_STATUSES = frozenset({401, 403})
HTTP_NOT_FOUND = 404

type Sleep = Callable[[float], Awaitable[None]]


def create_http_client(
    base_url: str,
    token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """HTTP-клиент для панели: адрес `.../api`, токен, свой User-Agent, таймаут."""
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/api",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        transport=transport,
    )


def _translate(error: Exception) -> RemnawaveError:
    """Ошибки SDK, httpx и pydantic → наши ошибки. Секретов в тексте нет."""
    if isinstance(error, httpx.RequestError):
        # SDK пропускает сетевые ошибки httpx как есть (не превращает в ApiError)
        return RemnawaveUnavailable(f"Панель недоступна: {type(error).__name__}")
    if isinstance(error, pydantic.ValidationError):
        return RemnawaveResponseError(f"Ответ панели не той формы: {error.error_count()} ошибок")
    if isinstance(error, ApiError):
        status = error.status_code
        if status == 0 or status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR:
            return RemnawaveUnavailable(f"Панель ответила HTTP {status}")
        if status in AUTH_STATUSES:
            return RemnawaveAuthError(f"Панель не приняла токен: HTTP {status}")
        if status == HTTP_NOT_FOUND:
            return AccountNotFound(error.message)
        return RemnawaveRejected(error.message, status_code=status, code=_error_code(error))
    raise TypeError(f"Неожиданная ошибка: {type(error).__name__}") from error


def _error_code(error: ApiError) -> str | None:
    code = error.code
    return None if code is None else str(getattr(code, "value", code))


def _to_account(user: UserResponseDto) -> ProxyAccount:
    return ProxyAccount(
        id=user.id,
        username=user.username,
        telegram_id=user.telegram_id,
        status=AccountStatus(user.status.value),
        expire_at=user.expire_at,
        traffic_used_bytes=int(user.user_traffic.used_traffic_bytes),
        traffic_limit_bytes=int(user.traffic_limit_bytes),
        device_limit=user.hwid_device_limit,
        first_connected_at=user.user_traffic.first_connected_at,
        squad_names=frozenset(squad.name for squad in user.active_internal_squads),
        subscription_url=user.subscription_url,
    )


def _to_device(device: HwidDeviceDto) -> Device:
    return Device(
        hwid=device.hwid,
        platform=device.platform,
        os_version=device.os_version,
        device_model=device.device_model,
        created_at=device.created_at,
    )


def _to_node(node: NodeResponseDto) -> Node:
    return Node(
        uuid=str(node.uuid),
        name=node.name,
        is_connected=node.is_connected,
        is_disabled=node.is_disabled,
        last_status_change=node.last_status_change,
        last_status_message=node.last_status_message,
        users_online=node.users_online,
    )


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError("Дата должна быть с часовым поясом (UTC)")


class RemnawaveApiClient:
    """Обёртка над `remnawave-api`. Реализует `RemnawaveClient`."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        mode: RunMode,
        *,
        prod_squad_name: str,
        sleep: Sleep = asyncio.sleep,
        max_attempts: int = MAX_ATTEMPTS,
        retry_delay: float = RETRY_DELAY_SECONDS,
    ) -> None:
        self._http = http
        self._sdk = RemnawaveSDK(client=http)
        self._guard = AccountGuard(mode)
        self._mode = mode
        # Squad для новых аккаунтов: в разработке — всегда `test`
        self._new_account_squad = DEV_SQUAD_NAME if mode is RunMode.DEV else prod_squad_name
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._squad_uuids: dict[str, str] | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- Повторы и перевод ошибок ---

    async def _call[T](self, request: Callable[[], Awaitable[T]], *, retry: bool) -> T:
        """Выполняет запрос; при временной ошибке повторяет (только если `retry`)."""
        attempts = self._max_attempts if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                return await request()
            except (httpx.RequestError, ApiError, pydantic.ValidationError) as error:
                translated = _translate(error)
                if not isinstance(translated, RemnawaveUnavailable) or attempt == attempts:
                    raise translated from error
                logger.warning(
                    "Remnawave: временная ошибка (%s), попытка %d из %d",
                    translated,
                    attempt,
                    attempts,
                )
            await self._sleep(self._retry_delay * attempt)
        raise AssertionError("недостижимо: цикл всегда возвращает или бросает")

    # --- Чтение с проверкой «аккаунт наш» ---

    async def _fetch_user(self, username: str) -> UserResponseDto:
        return await self._call(lambda: self._sdk.users.get_user_by_username(username), retry=True)

    async def _own_account(self, telegram_id: int) -> ProxyAccount:
        """Перечитывает аккаунт и проверяет, что его можно трогать."""
        username = self._guard.username_for(telegram_id)
        account = _to_account(await self._fetch_user(username))
        self._guard.ensure_own(account.username, account.squad_names)
        if account.username != username:
            raise RemnawaveResponseError("Панель вернула другой аккаунт")
        return account

    async def _squad_uuid(self, name: str) -> str:
        if self._squad_uuids is None:
            response = await self._call(
                lambda: self._sdk.internal_squads.get_internal_squads(), retry=True
            )
            self._squad_uuids = {s.name: str(s.uuid) for s in response.internal_squads}
        try:
            return self._squad_uuids[name]
        except KeyError:
            raise RemnawaveRejected(
                f"Squad {name!r} в панели не найден", status_code=0, code=None
            ) from None

    # --- Интерфейс RemnawaveClient ---

    async def check_panel_version(self) -> str:
        metadata = await self._call(lambda: self._sdk.system.get_metadata(), retry=True)
        version = metadata.version
        major = version.split(".", 1)[0]
        if not major.isdigit() or int(major) != SUPPORTED_PANEL_MAJOR:
            raise UnexpectedPanelVersion(
                f"Панель {version}, а бот написан под {SUPPORTED_PANEL_MAJOR}.x"
            )
        if version != TESTED_PANEL_VERSION:
            logger.warning("Панель Remnawave %s, бот проверен на %s", version, TESTED_PANEL_VERSION)
        return version

    async def get_account(self, telegram_id: int) -> ProxyAccount:
        return await self._own_account(telegram_id)

    async def get_or_create_account(
        self,
        telegram_id: int,
        *,
        expire_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
    ) -> ProxyAccount:
        _require_aware(expire_at)
        username = self._guard.username_for(telegram_id)
        try:
            return await self._own_account(telegram_id)
        except AccountNotFound:
            # нормальная ситуация: аккаунта ещё нет — создаём ниже
            logger.info("Прокси-аккаунта %s нет, создаём", username)
        body = CreateUserBodyDto(
            username=username,
            expire_at=expire_at,
            telegram_id=telegram_id,
            hwid_device_limit=device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
            active_internal_squads=[await self._squad_uuid(self._new_account_squad)],
        )
        try:
            # Создание не повторяем вслепую: повтор после таймаута дал бы «уже есть»
            user = await self._call(lambda: self._sdk.users.create_user(body), retry=False)
        except RemnawaveRejected as error:
            if error.code != USERNAME_EXISTS_CODE:
                raise
            # Гонка «Попробовать» и «Купить»: аккаунт успели создать — берём его
            return await self._own_account(telegram_id)
        account = _to_account(user)
        self._guard.ensure_own(account.username, account.squad_names)
        return account

    async def _update(self, telegram_id: int, **changes: object) -> ProxyAccount:
        account = await self._own_account(telegram_id)
        body = UpdateUserBodyDto(id=account.id, **changes)
        # PATCH с абсолютными значениями: повтор безопасен (аудит §6.2)
        user = await self._call(lambda: self._sdk.users.update_user(body), retry=True)
        return _to_account(user)

    async def set_expiry(self, telegram_id: int, expire_at: datetime) -> ProxyAccount:
        _require_aware(expire_at)
        return await self._update(telegram_id, expire_at=expire_at)

    async def set_limits(
        self, telegram_id: int, *, device_limit: int, traffic_limit_bytes: int
    ) -> ProxyAccount:
        return await self._update(
            telegram_id, hwid_device_limit=device_limit, traffic_limit_bytes=traffic_limit_bytes
        )

    async def add_to_squad(self, telegram_id: int, squad_name: str) -> ProxyAccount:
        account = await self._own_account(telegram_id)
        if squad_name in account.squad_names:
            return account
        uuids = [await self._squad_uuid(name) for name in sorted(account.squad_names)]
        uuids.append(await self._squad_uuid(squad_name))
        body = UpdateUserBodyDto(id=account.id, active_internal_squads=uuids)
        user = await self._call(lambda: self._sdk.users.update_user(body), retry=True)
        return _to_account(user)

    async def _switch(self, telegram_id: int, *, enable: bool) -> ProxyAccount:
        account = await self._own_account(telegram_id)
        action = self._sdk.users.enable_user if enable else self._sdk.users.disable_user
        try:
            user = await self._call(lambda: action(account.id), retry=False)
        except RemnawaveRejected as error:
            already = ALREADY_ENABLED_CODE if enable else ALREADY_DISABLED_CODE
            if error.code != already:
                raise
            return await self._own_account(telegram_id)  # уже в нужном состоянии
        return _to_account(user)

    async def enable_account(self, telegram_id: int) -> ProxyAccount:
        return await self._switch(telegram_id, enable=True)

    async def disable_account(self, telegram_id: int) -> ProxyAccount:
        return await self._switch(telegram_id, enable=False)

    async def list_devices(self, telegram_id: int) -> list[Device]:
        account = await self._own_account(telegram_id)
        response = await self._call(lambda: self._sdk.hwid.get_hwid_user(account.id), retry=True)
        return [_to_device(d) for d in response.devices]

    async def delete_device(self, telegram_id: int, hwid: str) -> list[Device]:
        account = await self._own_account(telegram_id)
        body = HWIDDeleteRequest(user_id=account.id, hwid=hwid)
        response = await self._call(lambda: self._sdk.hwid.delete_hwid_to_user(body), retry=False)
        return [_to_device(d) for d in response.devices]

    async def revoke_subscription(self, telegram_id: int) -> ProxyAccount:
        account = await self._own_account(telegram_id)
        # Каждый вызов выдаёт новую ссылку — не повторяем
        user = await self._call(
            lambda: self._sdk.users.revoke_user_subscription(
                account.id, RevokeUserSubscriptionBodyDto()
            ),
            retry=False,
        )
        return _to_account(user)

    async def list_accounts(self) -> list[ProxyAccount]:
        accounts: list[ProxyAccount] = []
        cursor: int | None = None
        while True:
            page = await self._call(
                lambda c=cursor: self._sdk.users.get_users_stream(size=STREAM_PAGE_SIZE, cursor=c),
                retry=True,
            )
            for user in page.users:
                account = _to_account(user)
                if self._guard.is_own(account.username, account.squad_names):
                    accounts.append(account)
            if not page.has_more or page.next_cursor is None:
                return accounts
            cursor = page.next_cursor

    async def list_nodes(self) -> list[Node]:
        nodes = await self._call(lambda: self._sdk.nodes.get_all_nodes(), retry=True)
        return [_to_node(n) for n in nodes.root]

    async def delete_account(self, telegram_id: int) -> None:
        if self._mode is not RunMode.DEV:
            raise OperationNotAllowed("В боевом режиме прокси-аккаунты не удаляются")
        account = await self._own_account(telegram_id)
        await self._call(lambda: self._sdk.users.delete_user(account.id), retry=False)
