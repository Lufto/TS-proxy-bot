"""Настоящий клиент: какие запросы уходят в панель и как разбираются её ответы.

Форма ответов — по контракту панели 3.4.5 (см. `panel_responses.py`).
"""

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from mock_panel import TOKEN, MockPanel, fail, ok
from panel_responses import (
    DEFAULT_SQUAD_UUID,
    TEST_SQUAD_UUID,
    device,
    error,
    metadata,
    node,
    squads,
    user,
)

from tsproxy.config import RunMode
from tsproxy.integrations.remnawave import USER_AGENT, RemnawaveApiClient
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    RemnawaveAuthError,
    RemnawaveRejected,
    RemnawaveResponseError,
    RemnawaveUnavailable,
    UnexpectedPanelVersion,
)

pytestmark = pytest.mark.anyio

type MakeClient = Callable[[RunMode], RemnawaveApiClient]

USER_PATH = "/api/users/by-username/tg_100"
EXPIRE = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)


async def test_headers_token_and_user_agent(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    await make_client(RunMode.PROD).get_account(100)
    request = panel.requests[0]
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    # Cloudflare блокирует стандартный User-Agent Python — шлём свой
    assert request.headers["User-Agent"] == USER_AGENT
    assert request.url.host == "panel.example"


async def test_get_account_maps_fields(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on(
        "GET",
        USER_PATH,
        ok(
            user(
                status="LIMITED",
                first_connected_at="2026-10-20T08:30:00.000Z",
                used_bytes=1_500,
                traffic_limit_bytes=7 * 1024**3,
            )
        ),
    )
    account = await make_client(RunMode.PROD).get_account(100)
    assert account.id == 7
    assert account.username == "tg_100"
    assert account.telegram_id == 100
    assert account.status is AccountStatus.LIMITED
    assert account.expire_at == datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
    assert account.traffic_used_bytes == 1_500
    assert account.traffic_limit_bytes == 7 * 1024**3
    assert account.device_limit == 3
    assert account.ever_connected
    assert account.first_connected_at == datetime(2026, 10, 20, 8, 30, tzinfo=UTC)
    assert account.squad_names == {"Default-Squad"}
    assert account.subscription_url == "https://sub.ts-proxy.com/AbCdEfGh12345678"


async def test_subscription_url_not_in_repr(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    account = await make_client(RunMode.PROD).get_account(100)
    assert "AbCdEfGh12345678" not in repr(account)


async def test_never_connected(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user(first_connected_at=None)))
    account = await make_client(RunMode.PROD).get_account(100)
    assert not account.ever_connected


async def test_not_found(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, fail(404, error("A025", "User not found")))
    with pytest.raises(AccountNotFound):
        await make_client(RunMode.PROD).get_account(100)


async def test_create_when_missing(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, fail(404, error("A025", "User not found")))
    panel.on("GET", "/api/internal-squads", ok(squads()))
    panel.on("POST", "/api/users", ok(user(), status=201))
    account = await make_client(RunMode.PROD).get_or_create_account(
        100, expire_at=EXPIRE, device_limit=3, traffic_limit_bytes=7 * 1024**3
    )
    assert account.username == "tg_100"
    body = panel.body()
    assert body["username"] == "tg_100"
    assert body["telegramId"] == 100
    assert body["expireAt"] == "2026-12-01T12:00:00Z"
    assert body["hwidDeviceLimit"] == 3
    assert body["trafficLimitBytes"] == 7 * 1024**3
    assert body["activeInternalSquads"] == [DEFAULT_SQUAD_UUID]


async def test_existing_account_returned_unchanged(
    panel: MockPanel, make_client: MakeClient
) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    account = await make_client(RunMode.PROD).get_or_create_account(
        100, expire_at=EXPIRE, device_limit=3, traffic_limit_bytes=0
    )
    assert account.id == 7
    assert panel.writes == []


async def test_username_exists_race_fetches_existing(
    panel: MockPanel, make_client: MakeClient
) -> None:
    # Между проверкой и созданием аккаунт успели создать: панель отвечает A019 (HTTP 400)
    panel.on("GET", USER_PATH, fail(404, error("A025", "User not found")), ok(user(user_id=9)))
    panel.on("GET", "/api/internal-squads", ok(squads()))
    panel.on("POST", "/api/users", fail(400, error("A019", "User username already exists")))
    account = await make_client(RunMode.PROD).get_or_create_account(
        100, expire_at=EXPIRE, device_limit=3, traffic_limit_bytes=0
    )
    assert account.id == 9


async def test_dev_account_created_in_test_squad(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", "/api/users/by-username/dev_100", fail(404, error("A025", "User not found")))
    panel.on("GET", "/api/internal-squads", ok(squads()))
    panel.on("POST", "/api/users", ok(user(username="dev_100", squads=("test",)), status=201))
    account = await make_client(RunMode.DEV).get_or_create_account(
        100, expire_at=EXPIRE, device_limit=3, traffic_limit_bytes=0
    )
    assert account.username == "dev_100"
    assert panel.body()["username"] == "dev_100"
    assert panel.body()["activeInternalSquads"] == [TEST_SQUAD_UUID]


async def test_set_expiry_is_absolute_patch(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("PATCH", "/api/users", ok(user(expire_at="2026-12-01T12:00:00.000Z")))
    account = await make_client(RunMode.PROD).set_expiry(100, EXPIRE)
    assert panel.body() == {"id": 7, "expireAt": "2026-12-01T12:00:00Z"}
    assert account.expire_at == EXPIRE


async def test_set_expiry_rejects_naive_datetime(make_client: MakeClient) -> None:
    with pytest.raises(ValueError, match="часовым поясом"):
        await make_client(RunMode.PROD).set_expiry(100, datetime(2026, 12, 1))  # noqa: DTZ001


async def test_set_limits(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("PATCH", "/api/users", ok(user()))
    await make_client(RunMode.PROD).set_limits(100, device_limit=3, traffic_limit_bytes=0)
    assert panel.body() == {"id": 7, "hwidDeviceLimit": 3, "trafficLimitBytes": 0}


async def test_reset_traffic(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user(status="LIMITED")))
    panel.on("POST", "/api/users/7/actions/reset-traffic", ok(user(status="ACTIVE")))
    account = await make_client(RunMode.PROD).reset_traffic(100)
    assert account.status is AccountStatus.ACTIVE
    assert [r.url.path for r in panel.writes] == ["/api/users/7/actions/reset-traffic"]


async def test_disable_and_enable(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("POST", "/api/users/7/actions/disable", ok(user(status="DISABLED")))
    panel.on("POST", "/api/users/7/actions/enable", ok(user(status="ACTIVE")))
    client = make_client(RunMode.PROD)
    assert (await client.disable_account(100)).status is AccountStatus.DISABLED
    assert (await client.enable_account(100)).status is AccountStatus.ACTIVE


async def test_disable_already_disabled_is_not_error(
    panel: MockPanel, make_client: MakeClient
) -> None:
    panel.on("GET", USER_PATH, ok(user(status="DISABLED")))
    panel.on(
        "POST", "/api/users/7/actions/disable", fail(400, error("A029", "User already disabled"))
    )
    account = await make_client(RunMode.PROD).disable_account(100)
    assert account.status is AccountStatus.DISABLED


async def test_add_to_squad_keeps_existing(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("GET", "/api/internal-squads", ok(squads()))
    panel.on("PATCH", "/api/users", ok(user(squads=("Default-Squad", "test"))))
    account = await make_client(RunMode.PROD).add_to_squad(100, "test")
    assert panel.body() == {"id": 7, "activeInternalSquads": [DEFAULT_SQUAD_UUID, TEST_SQUAD_UUID]}
    assert account.squad_names == {"Default-Squad", "test"}


async def test_add_to_unknown_squad(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("GET", "/api/internal-squads", ok(squads()))
    with pytest.raises(RemnawaveRejected, match="не найден"):
        await make_client(RunMode.PROD).add_to_squad(100, "nope")
    assert panel.writes == []


async def test_list_and_delete_devices(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("GET", "/api/hwid/devices/7", ok({"total": 2, "devices": [device("a"), device("b")]}))
    panel.on("POST", "/api/hwid/devices/delete", ok({"total": 1, "devices": [device("b")]}))
    client = make_client(RunMode.PROD)
    devices = await client.list_devices(100)
    assert [d.hwid for d in devices] == ["a", "b"]
    assert devices[0].device_model == "iPhone 15"
    assert devices[0].platform == "iOS"
    assert devices[0].os_version == "18.1"
    assert devices[0].created_at == datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
    remaining = await client.delete_device(100, "a")
    assert panel.body() == {"userId": 7, "hwid": "a"}
    assert [d.hwid for d in remaining] == ["b"]


async def test_revoke_subscription(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("POST", "/api/users/7/actions/revoke", ok(user(short_uuid="NewShortUuid9999")))
    account = await make_client(RunMode.PROD).revoke_subscription(100)
    assert panel.body() == {}
    assert account.subscription_url.endswith("NewShortUuid9999")


async def test_list_nodes(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", "/api/nodes", ok([node("NL"), node("DE", connected=False)]))
    nodes = await make_client(RunMode.PROD).list_nodes()
    assert [(n.name, n.is_connected) for n in nodes] == [("NL", True), ("DE", False)]
    assert nodes[1].last_status_message == "Connection lost"
    assert nodes[0].users_online == 4


async def test_list_accounts_pages_through_stream(
    panel: MockPanel, make_client: MakeClient
) -> None:
    pages = [
        ok({"users": [user(user_id=1, username="tg_1")], "nextCursor": "1", "hasMore": True}),
        ok({"users": [user(user_id=2, username="tg_2")], "nextCursor": None, "hasMore": False}),
    ]
    panel.on("GET", "/api/users/stream", *pages)
    accounts = await make_client(RunMode.PROD).list_accounts()
    assert [a.username for a in accounts] == ["tg_1", "tg_2"]
    assert panel.requests[1].url.params["cursor"] == "1"


@pytest.mark.parametrize("version", ["3.4.5", "3.5.0"])
async def test_version_accepted(panel: MockPanel, make_client: MakeClient, version: str) -> None:
    panel.on("GET", "/api/system/metadata", ok(metadata(version)))
    assert await make_client(RunMode.PROD).check_panel_version() == version


@pytest.mark.parametrize("version", ["2.7.4", "4.0.0", "dev"])
async def test_version_refused(panel: MockPanel, make_client: MakeClient, version: str) -> None:
    panel.on("GET", "/api/system/metadata", ok(metadata(version)))
    with pytest.raises(UnexpectedPanelVersion):
        await make_client(RunMode.PROD).check_panel_version()


# --- Ошибки, таймауты, повторы ---


async def test_read_retried_after_server_error(
    panel: MockPanel, make_client: MakeClient, sleeps: list[float]
) -> None:
    panel.on("GET", USER_PATH, fail(502, {"message": "Bad gateway"}), ok(user()))
    account = await make_client(RunMode.PROD).get_account(100)
    assert account.id == 7
    assert len(sleeps) == 1


async def test_read_gives_up_after_limited_attempts(
    panel: MockPanel, make_client: MakeClient, sleeps: list[float]
) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    panel.on("GET", USER_PATH, timeout)
    with pytest.raises(RemnawaveUnavailable):
        await make_client(RunMode.PROD).get_account(100)
    assert len(panel.requests) == 3
    assert len(sleeps) == 2


async def test_network_error_is_unavailable(panel: MockPanel, make_client: MakeClient) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    panel.on("GET", "/api/nodes", refused)
    with pytest.raises(RemnawaveUnavailable):
        await make_client(RunMode.PROD).list_nodes()


async def test_create_not_retried(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, fail(404, error("A025", "User not found")))
    panel.on("GET", "/api/internal-squads", ok(squads()))
    panel.on("POST", "/api/users", fail(503, {"message": "down"}))
    with pytest.raises(RemnawaveUnavailable):
        await make_client(RunMode.PROD).get_or_create_account(
            100, expire_at=EXPIRE, device_limit=3, traffic_limit_bytes=0
        )
    assert len(panel.writes) == 1


async def test_revoke_not_retried(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("POST", "/api/users/7/actions/revoke", fail(500, {"message": "oops"}))
    with pytest.raises(RemnawaveUnavailable):
        await make_client(RunMode.PROD).revoke_subscription(100)
    assert len(panel.writes) == 1


async def test_set_expiry_retried(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on("PATCH", "/api/users", fail(500, {"message": "oops"}), ok(user()))
    await make_client(RunMode.PROD).set_expiry(100, EXPIRE)
    assert len(panel.writes) == 2


async def test_bad_token(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, fail(401, error("A003", "Unauthorized")))
    with pytest.raises(RemnawaveAuthError) as caught:
        await make_client(RunMode.PROD).get_account(100)
    assert TOKEN not in str(caught.value)
    assert len(panel.requests) == 1


async def test_rejected_request(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", USER_PATH, ok(user()))
    panel.on(
        "PATCH",
        "/api/users",
        fail(400, {"message": "Expiration date cannot be in the past", "statusCode": 400}),
    )
    with pytest.raises(RemnawaveRejected) as caught:
        await make_client(RunMode.PROD).set_expiry(100, EXPIRE)
    assert caught.value.status_code == 400


async def test_unexpected_response_shape(panel: MockPanel, make_client: MakeClient) -> None:
    broken = user()
    del broken["shortUuid"]
    panel.on("GET", USER_PATH, ok(broken))
    with pytest.raises(RemnawaveResponseError):
        await make_client(RunMode.PROD).get_account(100)
