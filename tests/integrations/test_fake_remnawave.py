"""Подделка Remnawave ведёт себя как панель там, где на это опираются сервисы."""

from datetime import UTC, datetime, timedelta

import pytest

from tsproxy.config import RunMode
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    Device,
    Node,
    OperationNotAllowed,
    RemnawaveClient,
    RemnawaveRejected,
    RemnawaveUnavailable,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
GIB = 1024**3


def make_fake(mode: RunMode = RunMode.PROD) -> FakeRemnawave:
    return FakeRemnawave(mode, now=lambda: NOW)


async def create(fake: FakeRemnawave, telegram_id: int = 100) -> None:
    await fake.get_or_create_account(
        telegram_id,
        expire_at=NOW + timedelta(days=7),
        device_limit=3,
        traffic_limit_bytes=7 * GIB,
    )


def test_fake_matches_interface() -> None:
    client: RemnawaveClient = make_fake()
    assert client is not None


async def test_create_and_get() -> None:
    fake = make_fake()
    await create(fake)
    account = await fake.get_account(100)
    assert account.username == "tg_100"
    assert account.telegram_id == 100
    assert account.status is AccountStatus.ACTIVE
    assert account.device_limit == 3
    assert account.traffic_limit_bytes == 7 * GIB
    assert account.squad_names == {"Default-Squad"}
    assert account.subscription_url.startswith("https://sub.ts-proxy.com/")
    assert not account.ever_connected


async def test_dev_creates_in_test_squad() -> None:
    fake = make_fake(RunMode.DEV)
    await create(fake)
    account = await fake.get_account(100)
    assert account.username == "dev_100"
    assert account.squad_names == {"test"}


async def test_get_or_create_is_idempotent() -> None:
    fake = make_fake()
    await create(fake)
    first = await fake.get_account(100)
    await fake.get_or_create_account(
        100, expire_at=NOW + timedelta(days=30), device_limit=5, traffic_limit_bytes=0
    )
    assert await fake.get_account(100) == first
    assert fake.writes == [("create", "tg_100")]


async def test_missing_account() -> None:
    with pytest.raises(AccountNotFound):
        await make_fake().get_account(1)


async def test_set_expiry_revives_expired() -> None:
    fake = make_fake()
    await create(fake)
    fake.set_status(100, AccountStatus.EXPIRED)
    target = NOW + timedelta(days=30)
    account = await fake.set_expiry(100, target)
    assert account.expire_at == target
    assert account.status is AccountStatus.ACTIVE
    # «до X» — повтор ничего не меняет
    assert (await fake.set_expiry(100, target)).expire_at == target


async def test_set_expiry_in_past_rejected() -> None:
    fake = make_fake()
    await create(fake)
    with pytest.raises(RemnawaveRejected):
        await fake.set_expiry(100, NOW - timedelta(minutes=1))


async def test_unlimited_traffic_lifts_limited() -> None:
    fake = make_fake()
    await create(fake)
    fake.set_traffic_used(100, 7 * GIB)
    fake.set_status(100, AccountStatus.LIMITED)
    account = await fake.set_limits(100, device_limit=3, traffic_limit_bytes=0)
    assert account.status is AccountStatus.ACTIVE
    assert account.traffic_limit_bytes == 0


async def test_reset_traffic_lifts_limited() -> None:
    fake = make_fake()
    await create(fake)
    fake.set_traffic_used(100, 7 * GIB)
    fake.set_status(100, AccountStatus.LIMITED)
    account = await fake.reset_traffic(100)
    assert account.status is AccountStatus.ACTIVE
    assert account.traffic_used_bytes == 0


async def test_disable_enable() -> None:
    fake = make_fake()
    await create(fake)
    assert (await fake.disable_account(100)).status is AccountStatus.DISABLED
    assert (await fake.disable_account(100)).status is AccountStatus.DISABLED
    assert (await fake.enable_account(100)).status is AccountStatus.ACTIVE


async def test_first_connection() -> None:
    fake = make_fake()
    await create(fake)
    fake.simulate_connection(100, NOW)
    account = await fake.get_account(100)
    assert account.ever_connected
    assert account.first_connected_at == NOW


async def test_devices() -> None:
    fake = make_fake()
    await create(fake)
    for hwid in ("a", "b"):
        fake.simulate_device(
            100,
            Device(
                hwid=hwid, platform="iOS", os_version="18", device_model="iPhone", created_at=NOW
            ),
        )
    assert [d.hwid for d in await fake.list_devices(100)] == ["a", "b"]
    assert [d.hwid for d in await fake.delete_device(100, "a")] == ["b"]


async def test_revoke_changes_link() -> None:
    fake = make_fake()
    await create(fake)
    before = (await fake.get_account(100)).subscription_url
    after = (await fake.revoke_subscription(100)).subscription_url
    assert before != after


async def test_add_to_squad() -> None:
    fake = make_fake()
    await create(fake)
    account = await fake.add_to_squad(100, "test")
    assert account.squad_names == {"Default-Squad", "test"}


async def test_nodes_and_version() -> None:
    fake = make_fake()
    fake.nodes = [
        Node(
            uuid="n1",
            name="NL",
            is_connected=False,
            is_disabled=False,
            last_status_change=NOW,
            last_status_message="lost",
            users_online=0,
        )
    ]
    assert [n.name for n in await fake.list_nodes()] == ["NL"]
    assert await fake.check_panel_version() == "3.4.5"


async def test_fail_next_simulates_outage() -> None:
    fake = make_fake()
    await create(fake)
    fake.fail_next(RemnawaveUnavailable("panel down"))
    with pytest.raises(RemnawaveUnavailable):
        await fake.get_account(100)
    assert (await fake.get_account(100)).username == "tg_100"


async def test_delete_only_in_dev() -> None:
    prod = make_fake()
    await create(prod)
    with pytest.raises(OperationNotAllowed):
        await prod.delete_account(100)
    dev = make_fake(RunMode.DEV)
    await create(dev)
    await dev.delete_account(100)
    with pytest.raises(AccountNotFound):
        await dev.get_account(100)


# --- Объявление в приложении (задача 35): заголовок `announce` в настройках подписки ---

PROFILE_HEADERS = {"profile-title": "TS Proxy", "support-url": "https://t.me/ts_proxy_support"}


async def test_announce_is_empty_at_start() -> None:
    assert await make_fake().get_announce() is None


async def test_set_announce_keeps_other_headers() -> None:
    fake = make_fake()
    fake.subscription_headers.update(PROFILE_HEADERS)

    previous = await fake.set_announce("base64:0J/RgNC40LLQtdGC")

    assert previous is None
    assert fake.subscription_headers == {**PROFILE_HEADERS, "announce": "base64:0J/RgNC40LLQtdGC"}
    assert await fake.get_announce() == "base64:0J/RgNC40LLQtdGC"
    assert fake.writes == [("set_announce", "subscription-settings")]


async def test_set_announce_replaces_previous_in_any_case() -> None:
    fake = make_fake()
    fake.subscription_headers["Announce"] = "old"

    previous = await fake.set_announce("new")

    assert previous == "old"
    assert fake.subscription_headers == {"announce": "new"}


async def test_clear_announce_keeps_other_headers() -> None:
    fake = make_fake()
    fake.subscription_headers.update({**PROFILE_HEADERS, "announce": "text"})

    assert await fake.clear_announce() == "text"

    assert fake.subscription_headers == PROFILE_HEADERS
    assert await fake.get_announce() is None


async def test_clear_without_announce_writes_nothing() -> None:
    fake = make_fake()
    fake.subscription_headers.update(PROFILE_HEADERS)

    assert await fake.clear_announce() is None

    assert fake.writes == []
    assert fake.subscription_headers == PROFILE_HEADERS


@pytest.mark.parametrize("value", ["", "Работы", "two\nlines"])
async def test_value_unfit_for_http_header_is_refused(value: str) -> None:
    # Русские буквы и перевод строки в заголовке HTTP сломали бы подписку всем клиентам
    fake = make_fake()
    with pytest.raises(ValueError):
        await fake.set_announce(value)
    assert fake.subscription_headers == {}


async def test_announce_outage() -> None:
    fake = make_fake()
    fake.fail_next(RemnawaveUnavailable("panel down"))
    with pytest.raises(RemnawaveUnavailable):
        await fake.set_announce("text")
    assert fake.subscription_headers == {}
