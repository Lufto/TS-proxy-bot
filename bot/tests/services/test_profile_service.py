"""Профиль и устройства: статус из Remnawave и базы, удаление только своих устройств."""

from datetime import timedelta

import pytest
from conftest import NOW

from tsproxy.config import RunMode
from tsproxy.db.models import Client
from tsproxy.domain.constants import BYTES_PER_GB
from tsproxy.domain.profile import ProfileStatus, TimeLeft
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus, Device, RemnawaveUnavailable
from tsproxy.services.fakes import FakeClock
from tsproxy.services.profile import DeviceSlots, ProfileService, TrafficUsage, device_ref

CLIENT_ID = 111
OTHER_CLIENT_ID = 222
TRIAL_TRAFFIC = 7 * BYTES_PER_GB
EXPIRE_AT = NOW + timedelta(days=5, hours=3)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def service(remnawave: FakeRemnawave, clock: FakeClock) -> ProfileService:
    return ProfileService(remnawave, clock)


def client_row(telegram_id: int = CLIENT_ID, **fields: object) -> Client:
    return Client(telegram_id=telegram_id, first_seen_at=NOW, **fields)


async def create_account(
    remnawave: FakeRemnawave,
    telegram_id: int = CLIENT_ID,
    *,
    traffic_limit_bytes: int = 0,
    device_limit: int = 3,
) -> None:
    await remnawave.get_or_create_account(
        telegram_id,
        expire_at=EXPIRE_AT,
        device_limit=device_limit,
        traffic_limit_bytes=traffic_limit_bytes,
    )


def device(hwid: str, *, model: str | None = "iPhone 15") -> Device:
    return Device(hwid=hwid, platform="iOS", os_version="18.1", device_model=model, created_at=NOW)


# --- Профиль ---


async def test_no_account_no_profile(service: ProfileService) -> None:
    assert await service.profile(client_row()) is None


async def test_paid_subscription(service: ProfileService, remnawave: FakeRemnawave) -> None:
    await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device("a"))

    profile = await service.profile(client_row())

    assert profile is not None
    assert profile.status is ProfileStatus.ACTIVE
    assert profile.expire_at == EXPIRE_AT
    assert profile.time_left == TimeLeft(days=5, hours=3)
    assert profile.devices == DeviceSlots(used=1, limit=3)
    assert profile.traffic is None  # трафик — только в пробном


async def test_trial_shows_traffic(service: ProfileService, remnawave: FakeRemnawave) -> None:
    await create_account(remnawave, traffic_limit_bytes=TRIAL_TRAFFIC)
    remnawave.set_traffic_used(CLIENT_ID, 2 * BYTES_PER_GB)

    profile = await service.profile(client_row(trial_granted_at=NOW))

    assert profile is not None
    assert profile.status is ProfileStatus.TRIAL
    assert profile.traffic == TrafficUsage(used_bytes=2 * BYTES_PER_GB, limit_bytes=TRIAL_TRAFFIC)


async def test_purchase_after_trial_is_paid(
    service: ProfileService, remnawave: FakeRemnawave
) -> None:
    # Покупка в пробный снимает лимит 7 ГБ (аудит §7.6) — дальше это оплаченный срок
    await create_account(remnawave, traffic_limit_bytes=0)

    profile = await service.profile(client_row(trial_granted_at=NOW - timedelta(days=3)))

    assert profile is not None
    assert profile.status is ProfileStatus.ACTIVE
    assert profile.traffic is None


async def test_trial_traffic_used_up(service: ProfileService, remnawave: FakeRemnawave) -> None:
    await create_account(remnawave, traffic_limit_bytes=TRIAL_TRAFFIC)
    remnawave.set_traffic_used(CLIENT_ID, TRIAL_TRAFFIC)
    remnawave.set_status(CLIENT_ID, AccountStatus.LIMITED)

    profile = await service.profile(client_row(trial_granted_at=NOW))

    assert profile is not None
    assert profile.status is ProfileStatus.TRIAL_TRAFFIC_USED_UP


async def test_grace_from_database_mark(service: ProfileService, remnawave: FakeRemnawave) -> None:
    await create_account(remnawave)

    profile = await service.profile(client_row(grace_started_at=NOW - timedelta(days=1)))

    assert profile is not None
    assert profile.status is ProfileStatus.GRACE


async def test_grace_extended_by_compensation_is_still_grace(
    service: ProfileService, remnawave: FakeRemnawave
) -> None:
    # задача 20: компенсация продлила льготный — срок в панели позже обычного конца льготного
    await create_account(remnawave)
    started = NOW - timedelta(days=4, hours=1)  # обычный льготный кончился час назад

    profile = await service.profile(client_row(grace_started_at=started))

    assert profile is not None
    assert profile.status is ProfileStatus.GRACE


async def test_expired(service: ProfileService, remnawave: FakeRemnawave, clock: FakeClock) -> None:
    await create_account(remnawave)
    clock.advance(timedelta(days=6))
    remnawave.set_status(CLIENT_ID, AccountStatus.EXPIRED)

    profile = await service.profile(client_row())

    assert profile is not None
    assert profile.status is ProfileStatus.EXPIRED
    assert profile.time_left == TimeLeft(days=0, hours=0)


async def test_disabled_with_time_left_is_suspended(
    service: ProfileService, remnawave: FakeRemnawave
) -> None:
    await create_account(remnawave)
    await remnawave.disable_account(CLIENT_ID)

    profile = await service.profile(client_row())

    assert profile is not None
    assert profile.status is ProfileStatus.SUSPENDED


async def test_panel_without_personal_limit(
    service: ProfileService, remnawave: FakeRemnawave
) -> None:
    # 0 в панели — «без лимита»: не пишем клиенту «1 из 0»
    await create_account(remnawave, device_limit=0)

    profile = await service.profile(client_row())

    assert profile is not None
    assert profile.devices == DeviceSlots(used=0, limit=None)


async def test_panel_unavailable_is_not_hidden(
    service: ProfileService, remnawave: FakeRemnawave
) -> None:
    await create_account(remnawave)
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    with pytest.raises(RemnawaveUnavailable):
        await service.profile(client_row())


# --- Устройства ---


async def test_device_list(service: ProfileService, remnawave: FakeRemnawave) -> None:
    await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device("a"))
    remnawave.simulate_device(CLIENT_ID, device("b", model=None))

    devices = await service.devices(CLIENT_ID)

    assert devices is not None
    assert [d.hwid for d in devices.devices] == ["a", "b"]
    assert devices.limit == 3


async def test_no_account_no_devices(service: ProfileService) -> None:
    assert await service.devices(CLIENT_ID) is None


async def test_delete_own_device(service: ProfileService, remnawave: FakeRemnawave) -> None:
    await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device("a"))
    remnawave.simulate_device(CLIENT_ID, device("b"))

    result = await service.delete_device(CLIENT_ID, device_ref("a"))

    assert result is not None
    assert result.deleted
    assert [d.hwid for d in result.devices.devices] == ["b"]
    assert [d.hwid for d in await remnawave.list_devices(CLIENT_ID)] == ["b"]


async def test_cannot_delete_device_of_another_client(
    service: ProfileService, remnawave: FakeRemnawave
) -> None:
    """Подделанная кнопка со ссылкой на чужое устройство: бот ищет его только среди
    устройств нажавшего — не находит и ничего не удаляет (аудит §4.3)."""
    await create_account(remnawave, CLIENT_ID)
    await create_account(remnawave, OTHER_CLIENT_ID)
    remnawave.simulate_device(CLIENT_ID, device("victim-device"))
    remnawave.simulate_device(OTHER_CLIENT_ID, device("own-device"))
    writes_before = list(remnawave.writes)

    result = await service.delete_device(OTHER_CLIENT_ID, device_ref("victim-device"))

    assert result is not None
    assert not result.deleted
    assert [d.hwid for d in result.devices.devices] == ["own-device"]
    assert [d.hwid for d in await remnawave.list_devices(CLIENT_ID)] == ["victim-device"]
    assert remnawave.writes == writes_before  # в панель ничего не ушло


async def test_deleting_already_deleted_device_changes_nothing(
    service: ProfileService, remnawave: FakeRemnawave
) -> None:
    # Двойное нажатие «🗑 Удалить»: второе ничего не меняет в панели
    await create_account(remnawave)
    remnawave.simulate_device(CLIENT_ID, device("a"))
    await service.delete_device(CLIENT_ID, device_ref("a"))
    writes_before = list(remnawave.writes)

    result = await service.delete_device(CLIENT_ID, device_ref("a"))

    assert result is not None
    assert not result.deleted
    assert remnawave.writes == writes_before


async def test_delete_without_account(service: ProfileService) -> None:
    assert await service.delete_device(CLIENT_ID, device_ref("a")) is None


def test_device_ref_is_short_and_stable() -> None:
    # Данные кнопки Telegram — до 64 байт, а HWID может быть длинным
    long_hwid = "x" * 500
    assert device_ref(long_hwid) == device_ref(long_hwid)
    assert len(device_ref(long_hwid)) == 16
    assert device_ref("a") != device_ref("b")
    assert "x" not in device_ref(long_hwid)  # сам HWID в кнопку не попадает
