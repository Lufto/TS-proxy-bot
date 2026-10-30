"""Подделки внешних сервисов для тестов и разработки без настоящей панели.

`FakeRemnawave` хранит аккаунты в памяти, ведёт себя как панель 3.4.x в тех местах,
на которые опирается бот, и применяет ту же защиту чужих аккаунтов (`AccountGuard`).
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tsproxy.config import RunMode
from tsproxy.integrations.remnawave_types import (
    ANNOUNCE_HEADER,
    DEV_SQUAD_NAME,
    AccountGuard,
    AccountNotFound,
    AccountStatus,
    Device,
    Node,
    OperationNotAllowed,
    ProxyAccount,
    RemnawaveError,
    RemnawaveRejected,
    check_header_value,
    find_header,
    without_header,
)

FAKE_SUBSCRIPTION_BASE = "https://sub.ts-proxy.com/"
FAKE_PANEL_VERSION = "3.4.5"
SHORT_UUID_BYTES = 12
# Что меняли в журнале `writes`, когда менялись настройки подписки, а не аккаунт
SUBSCRIPTION_SETTINGS = "subscription-settings"


def _new_short_uuid() -> str:
    return secrets.token_urlsafe(SHORT_UUID_BYTES)


@dataclass
class _StoredAccount:
    """Изменяемая запись подделки; наружу отдаётся как неизменяемый ProxyAccount."""

    id: int
    username: str
    telegram_id: int | None
    status: AccountStatus
    expire_at: datetime
    traffic_limit_bytes: int
    device_limit: int | None
    squad_names: set[str]
    short_uuid: str = field(default_factory=_new_short_uuid)
    traffic_used_bytes: int = 0
    first_connected_at: datetime | None = None
    devices: list[Device] = field(default_factory=list)

    def snapshot(self) -> ProxyAccount:
        return ProxyAccount(
            id=self.id,
            username=self.username,
            telegram_id=self.telegram_id,
            status=self.status,
            expire_at=self.expire_at,
            traffic_used_bytes=self.traffic_used_bytes,
            traffic_limit_bytes=self.traffic_limit_bytes,
            device_limit=self.device_limit,
            first_connected_at=self.first_connected_at,
            squad_names=frozenset(self.squad_names),
            subscription_url=FAKE_SUBSCRIPTION_BASE + self.short_uuid,
        )


class FakeRemnawave:
    """Подделка `RemnawaveClient` в памяти.

    Помощники для тестов: `add_foreign_account`, `simulate_connection`, `simulate_device`,
    `set_traffic_used`, `fail_next`; журнал изменений — `writes`; свои заголовки ответа
    подписки (настройки подписки панели) — `subscription_headers`.
    """

    def __init__(
        self,
        mode: RunMode = RunMode.PROD,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        prod_squad_name: str = "Default-Squad",
        version: str = FAKE_PANEL_VERSION,
    ) -> None:
        self._guard = AccountGuard(mode)
        self._mode = mode
        self._now = now
        self._new_account_squad = DEV_SQUAD_NAME if mode is RunMode.DEV else prod_squad_name
        self.version = version
        self.accounts: dict[str, _StoredAccount] = {}
        self.nodes: list[Node] = []
        self.subscription_headers: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []  # (действие, username) — что меняли
        self._next_id = 1
        self._pending_error: RemnawaveError | None = None

    # --- Помощники для тестов ---

    def add_foreign_account(
        self, username: str, *, squad_names: set[str] | None = None, telegram_id: int | None = None
    ) -> ProxyAccount:
        """Аккаунт «как у нынешних клиентов» — заведён руками, не ботом."""
        stored = self._store(
            username,
            telegram_id=telegram_id,
            expire_at=self._now(),
            device_limit=None,
            traffic_limit_bytes=0,
            squad_names=squad_names or {"Default-Squad"},
        )
        return stored.snapshot()

    def simulate_connection(self, telegram_id: int, at: datetime) -> None:
        stored = self._stored(telegram_id)
        stored.first_connected_at = stored.first_connected_at or at

    def simulate_device(self, telegram_id: int, device: Device) -> None:
        self._stored(telegram_id).devices.append(device)

    def set_traffic_used(self, telegram_id: int, used_bytes: int) -> None:
        self._stored(telegram_id).traffic_used_bytes = used_bytes

    def set_status(self, telegram_id: int, status: AccountStatus) -> None:
        """Например, EXPIRED — как сделала бы панель по окончании срока."""
        self._stored(telegram_id).status = status

    def fail_next(self, error: RemnawaveError) -> None:
        """Следующий вызов любого метода бросит эту ошибку (сбой панели)."""
        self._pending_error = error

    # --- Внутреннее ---

    def _raise_pending(self) -> None:
        if self._pending_error is not None:
            error, self._pending_error = self._pending_error, None
            raise error

    def _store(
        self,
        username: str,
        *,
        telegram_id: int | None,
        expire_at: datetime,
        device_limit: int | None,
        traffic_limit_bytes: int,
        squad_names: set[str],
    ) -> _StoredAccount:
        stored = _StoredAccount(
            id=self._next_id,
            username=username,
            telegram_id=telegram_id,
            status=AccountStatus.ACTIVE,
            expire_at=expire_at,
            traffic_limit_bytes=traffic_limit_bytes,
            device_limit=device_limit,
            squad_names=set(squad_names),
        )
        self._next_id += 1
        self.accounts[username] = stored
        return stored

    def _stored(self, telegram_id: int) -> _StoredAccount:
        username = self._guard.username_for(telegram_id)
        try:
            return self.accounts[username]
        except KeyError:
            raise AccountNotFound(f"User {username} not found") from None

    def _own(self, telegram_id: int) -> _StoredAccount:
        self._raise_pending()
        stored = self._stored(telegram_id)
        self._guard.ensure_own(stored.username, stored.squad_names)
        return stored

    def _check_future(self, expire_at: datetime) -> None:
        if expire_at.tzinfo is None:
            raise ValueError("Дата должна быть с часовым поясом (UTC)")
        if expire_at <= self._now():
            # Панель 3.x отклоняет срок в прошлом (update-user.command.ts)
            raise RemnawaveRejected(
                "Expiration date cannot be in the past", status_code=400, code=None
            )

    # --- Интерфейс RemnawaveClient ---

    async def check_panel_version(self) -> str:
        self._raise_pending()
        return self.version

    async def get_account(self, telegram_id: int) -> ProxyAccount:
        return self._own(telegram_id).snapshot()

    async def get_or_create_account(
        self,
        telegram_id: int,
        *,
        expire_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
    ) -> ProxyAccount:
        try:
            return self._own(telegram_id).snapshot()
        except AccountNotFound:
            username = self._guard.username_for(telegram_id)
        if expire_at.tzinfo is None:
            raise ValueError("Дата должна быть с часовым поясом (UTC)")
        stored = self._store(
            username,
            telegram_id=telegram_id,
            expire_at=expire_at,
            device_limit=device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
            squad_names={self._new_account_squad},
        )
        self.writes.append(("create", username))
        return stored.snapshot()

    async def set_expiry(self, telegram_id: int, expire_at: datetime) -> ProxyAccount:
        stored = self._own(telegram_id)
        self._check_future(expire_at)
        stored.expire_at = expire_at
        if stored.status is AccountStatus.EXPIRED:
            stored.status = AccountStatus.ACTIVE  # так делает панель при сроке в будущем
        self.writes.append(("set_expiry", stored.username))
        return stored.snapshot()

    async def set_limits(
        self, telegram_id: int, *, device_limit: int, traffic_limit_bytes: int
    ) -> ProxyAccount:
        stored = self._own(telegram_id)
        stored.device_limit = device_limit
        stored.traffic_limit_bytes = traffic_limit_bytes
        limit_lifted = traffic_limit_bytes == 0 or traffic_limit_bytes > stored.traffic_used_bytes
        if stored.status is AccountStatus.LIMITED and limit_lifted:
            stored.status = AccountStatus.ACTIVE
        self.writes.append(("set_limits", stored.username))
        return stored.snapshot()

    async def reset_traffic(self, telegram_id: int) -> ProxyAccount:
        stored = self._own(telegram_id)
        stored.traffic_used_bytes = 0
        if stored.status is AccountStatus.LIMITED:
            stored.status = AccountStatus.ACTIVE  # так делает панель после сброса трафика
        self.writes.append(("reset_traffic", stored.username))
        return stored.snapshot()

    async def enable_account(self, telegram_id: int) -> ProxyAccount:
        stored = self._own(telegram_id)
        if stored.status is AccountStatus.DISABLED:
            stored.status = AccountStatus.ACTIVE
            self.writes.append(("enable", stored.username))
        return stored.snapshot()

    async def disable_account(self, telegram_id: int) -> ProxyAccount:
        stored = self._own(telegram_id)
        if stored.status is not AccountStatus.DISABLED:
            stored.status = AccountStatus.DISABLED
            self.writes.append(("disable", stored.username))
        return stored.snapshot()

    async def add_to_squad(self, telegram_id: int, squad_name: str) -> ProxyAccount:
        stored = self._own(telegram_id)
        if squad_name not in stored.squad_names:
            stored.squad_names.add(squad_name)
            self.writes.append(("add_to_squad", stored.username))
        return stored.snapshot()

    async def list_devices(self, telegram_id: int) -> list[Device]:
        return list(self._own(telegram_id).devices)

    async def delete_device(self, telegram_id: int, hwid: str) -> list[Device]:
        stored = self._own(telegram_id)
        stored.devices = [d for d in stored.devices if d.hwid != hwid]
        self.writes.append(("delete_device", stored.username))
        return list(stored.devices)

    async def revoke_subscription(self, telegram_id: int) -> ProxyAccount:
        stored = self._own(telegram_id)
        stored.short_uuid = _new_short_uuid()
        self.writes.append(("revoke", stored.username))
        return stored.snapshot()

    async def list_accounts(self) -> list[ProxyAccount]:
        self._raise_pending()
        return [
            s.snapshot()
            for s in self.accounts.values()
            if self._guard.is_own(s.username, s.squad_names)
        ]

    async def list_nodes(self) -> list[Node]:
        self._raise_pending()
        return list(self.nodes)

    async def delete_account(self, telegram_id: int) -> None:
        if self._mode is not RunMode.DEV:
            raise OperationNotAllowed("В боевом режиме прокси-аккаунты не удаляются")
        stored = self._own(telegram_id)
        del self.accounts[stored.username]
        self.writes.append(("delete", stored.username))

    async def get_announce(self) -> str | None:
        self._raise_pending()
        return find_header(self.subscription_headers, ANNOUNCE_HEADER)

    async def set_announce(self, value: str) -> str | None:
        check_header_value(value)
        previous = await self.get_announce()
        headers = without_header(self.subscription_headers, ANNOUNCE_HEADER)
        # Панель весь словарь заменяет присланным, имена — маленькими буквами
        self.subscription_headers = {**headers, ANNOUNCE_HEADER: value}
        self.writes.append(("set_announce", SUBSCRIPTION_SETTINGS))
        return previous

    async def clear_announce(self) -> str | None:
        previous = await self.get_announce()
        if previous is None:
            return None
        self.subscription_headers = without_header(self.subscription_headers, ANNOUNCE_HEADER)
        self.writes.append(("clear_announce", SUBSCRIPTION_SETTINGS))
        return previous
