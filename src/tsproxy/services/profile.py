"""«👤 Мой профиль» и «📱 Мои устройства»: данные из Remnawave и отметок в базе.

Срок, статус, трафик и устройства знает только Remnawave; из базы — только то, чего там
нет (выдан ли пробный, начался ли льготный). Устройство удаляется, только если оно есть
в списке прокси-аккаунта нажавшего (аудит §4.3): подделанная кнопка чужое не удалит.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

from tsproxy.db.models import Client
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import UNLIMITED_TRAFFIC_BYTES
from tsproxy.domain.profile import ProfileStatus, TimeLeft, profile_status, time_left
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    Device,
    ProxyAccount,
    RemnawaveClient,
)
from tsproxy.services.clock import Clock, utc_now

logger = logging.getLogger(__name__)

# Длина ссылки на устройство в данных кнопки (64 бита хеша HWID): данные кнопки — до 64 байт
DEVICE_REF_LENGTH = 16


def device_ref(hwid: str) -> str:
    """Короткая ссылка на устройство для кнопки «🗑 Удалить».

    HWID бывает длинным и в кнопку не влезает. Ссылка сравнивается только с устройствами
    нажавшего, поэтому совпадение хешей у разных клиентов ничего не даёт.
    """
    return hashlib.sha256(hwid.encode()).hexdigest()[:DEVICE_REF_LENGTH]


@dataclass(frozen=True)
class DeviceSlots:
    """Устройства «N из M». `limit` — None, если личного лимита в панели нет."""

    used: int
    limit: int | None


@dataclass(frozen=True)
class TrafficUsage:
    used_bytes: int
    limit_bytes: int


@dataclass(frozen=True)
class Profile:
    """Всё, что показывает «👤 Мой профиль». `traffic` — только в пробном периоде."""

    status: ProfileStatus
    expire_at: datetime
    time_left: TimeLeft
    devices: DeviceSlots
    traffic: TrafficUsage | None


@dataclass(frozen=True)
class DeviceList:
    devices: tuple[Device, ...]
    limit: int | None


@dataclass(frozen=True)
class DeviceDeletion:
    """Итог «🗑 Удалить». `deleted=False` — такого устройства у клиента нет (уже удалено)."""

    deleted: bool
    devices: DeviceList


def _device_limit(account: ProxyAccount) -> int | None:
    # 0 — «без лимита», None — общий лимит панели: «N из M» показать не можем
    return account.device_limit or None


def on_trial(client: Client, account: ProxyAccount) -> bool:
    """Пробный: пробный выдавался, а лимит трафика ещё стоит.

    Тем же правилом пользуются напоминания (`services/reminders.py`).
    Покупка снимает лимит 7 ГБ (аудит §7.6), у оплаченного срока трафик без лимита.
    """
    limited = account.traffic_limit_bytes != UNLIMITED_TRAFFIC_BYTES
    return client.trial_granted_at is not None and limited


class ProfileService:
    """Профиль и устройства клиента. Клиент — всегда по Telegram ID нажавшего."""

    def __init__(self, remnawave: RemnawaveClient, clock: Clock) -> None:
        self._remnawave = remnawave
        self._clock = clock

    async def _account(self, telegram_id: int) -> ProxyAccount | None:
        try:
            return await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            return None  # нормальная ситуация: пробный не брал и не покупал

    async def profile(self, client: Client) -> Profile | None:
        """Профиль клиента. `None` — прокси-аккаунта нет. Сбой панели не скрываем."""
        account = await self._account(client.telegram_id)
        if account is None:
            return None
        devices = await self._remnawave.list_devices(client.telegram_id)
        now = utc_now(self._clock)
        trial = on_trial(client, account)
        snapshot = ClientSnapshot(
            expire_at=account.expire_at,
            on_trial=trial,
            grace_started_at=client.grace_started_at,
            access_disabled=account.status is AccountStatus.DISABLED,
        )
        traffic_used_up = account.status is AccountStatus.LIMITED
        traffic = (
            TrafficUsage(account.traffic_used_bytes, account.traffic_limit_bytes) if trial else None
        )
        return Profile(
            status=profile_status(snapshot, now, traffic_used_up=traffic_used_up),
            expire_at=account.expire_at,
            time_left=time_left(now, account.expire_at),
            devices=DeviceSlots(used=len(devices), limit=_device_limit(account)),
            traffic=traffic,
        )

    async def devices(self, telegram_id: int) -> DeviceList | None:
        """Устройства клиента. `None` — прокси-аккаунта нет."""
        account = await self._account(telegram_id)
        if account is None:
            return None
        devices = await self._remnawave.list_devices(telegram_id)
        return DeviceList(tuple(devices), _device_limit(account))

    async def delete_device(self, telegram_id: int, ref: str) -> DeviceDeletion | None:
        """Удалить устройство по ссылке из кнопки — только из списка самого клиента.

        `None` — прокси-аккаунта нет.
        """
        current = await self.devices(telegram_id)
        if current is None:
            return None
        device = next((d for d in current.devices if device_ref(d.hwid) == ref), None)
        if device is None:
            logger.info("Клиент %s: устройства %s в его списке нет", telegram_id, ref)
            return DeviceDeletion(deleted=False, devices=current)
        remaining = await self._remnawave.delete_device(telegram_id, device.hwid)
        logger.info("Клиент %s удалил устройство %s", telegram_id, ref)
        return DeviceDeletion(deleted=True, devices=DeviceList(tuple(remaining), current.limit))
