"""Защита нынешних клиентов: обёртка не меняет и не показывает чужие прокси-аккаунты.

Боевой режим — только `tg_<цифры>`; разработка — только `dev_<цифры>` в squad `test`.
Для настоящего клиента проверяем главное: до изменяющего запроса дело не доходит.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from mock_panel import MockPanel, ok
from panel_responses import user

from tsproxy.config import RunMode
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave import RemnawaveApiClient
from tsproxy.integrations.remnawave_types import (
    AccountGuard,
    ForeignAccountError,
    OperationNotAllowed,
    RemnawaveClient,
    RemnawaveResponseError,
)

pytestmark = pytest.mark.anyio

type MakeClient = Callable[[RunMode], RemnawaveApiClient]
EXPIRE = datetime(2026, 12, 1, tzinfo=UTC)

# Все изменяющие действия обёртки — каждое должно упираться в защиту
WRITE_ACTIONS: dict[str, Callable[[RemnawaveClient, int], Awaitable[object]]] = {
    "set_expiry": lambda c, tid: c.set_expiry(tid, EXPIRE),
    "set_limits": lambda c, tid: c.set_limits(tid, device_limit=3, traffic_limit_bytes=0),
    "enable": lambda c, tid: c.enable_account(tid),
    "disable": lambda c, tid: c.disable_account(tid),
    "add_to_squad": lambda c, tid: c.add_to_squad(tid, "test"),
    "delete_device": lambda c, tid: c.delete_device(tid, "hwid-1"),
    "revoke": lambda c, tid: c.revoke_subscription(tid),
    "get_or_create": lambda c, tid: c.get_or_create_account(
        tid, expire_at=EXPIRE, device_limit=3, traffic_limit_bytes=0
    ),
    "delete": lambda c, tid: c.delete_account(tid),
}


# --- Правила AccountGuard ---


@pytest.mark.parametrize(
    ("username", "squads", "own"),
    [
        ("tg_123", {"Default-Squad"}, True),
        ("tg_123", set(), True),
        ("alex", {"Default-Squad"}, False),
        ("tg_", {"Default-Squad"}, False),
        ("tg_12a", {"Default-Squad"}, False),
        ("TG_123", {"Default-Squad"}, False),
        ("dev_123", {"test"}, False),
        ("x_tg_123", {"Default-Squad"}, False),
    ],
)
def test_prod_rules(username: str, squads: set[str], own: bool) -> None:
    assert AccountGuard(RunMode.PROD).is_own(username, squads) is own


@pytest.mark.parametrize(
    ("username", "squads", "own"),
    [
        ("dev_123", {"test"}, True),
        ("dev_123", {"test", "Default-Squad"}, True),
        ("dev_123", {"Default-Squad"}, False),  # префикс верный, но вне squad test
        ("dev_123", set(), False),
        ("tg_123", {"test"}, False),  # боевой аккаунт в разработке не трогаем
        ("alex", {"test"}, False),
        ("dev_abc", {"test"}, False),
    ],
)
def test_dev_rules(username: str, squads: set[str], own: bool) -> None:
    assert AccountGuard(RunMode.DEV).is_own(username, squads) is own


def test_username_by_mode() -> None:
    assert AccountGuard(RunMode.PROD).username_for(42) == "tg_42"
    assert AccountGuard(RunMode.DEV).username_for(42) == "dev_42"


@pytest.mark.parametrize("telegram_id", [0, -5])
def test_invalid_telegram_id(telegram_id: int) -> None:
    with pytest.raises(ForeignAccountError):
        AccountGuard(RunMode.PROD).username_for(telegram_id)


# --- Настоящий клиент: ни одного изменяющего запроса к чужому аккаунту ---


@pytest.mark.parametrize("action", list(WRITE_ACTIONS))
async def test_dev_refuses_dev_account_outside_test_squad(
    panel: MockPanel, make_client: MakeClient, action: str
) -> None:
    # `dev_5` заведён руками и не в squad test — это не наш аккаунт
    panel.on(
        "GET", "/api/users/by-username/dev_5", ok(user(username="dev_5", squads=("Default-Squad",)))
    )
    with pytest.raises(ForeignAccountError):
        await WRITE_ACTIONS[action](make_client(RunMode.DEV), 5)
    assert panel.writes == []


@pytest.mark.parametrize("action", list(WRITE_ACTIONS))
async def test_invalid_id_refused_before_any_request(
    panel: MockPanel, make_client: MakeClient, action: str
) -> None:
    with pytest.raises((ForeignAccountError, OperationNotAllowed)):
        await WRITE_ACTIONS[action](make_client(RunMode.PROD), -1)
    assert panel.requests == []


async def test_prod_refuses_when_panel_returns_other_account(
    panel: MockPanel, make_client: MakeClient
) -> None:
    # Панель по имени tg_5 вдруг вернула ручной аккаунт — не меняем
    panel.on("GET", "/api/users/by-username/tg_5", ok(user(username="alex")))
    with pytest.raises(ForeignAccountError):
        await make_client(RunMode.PROD).set_expiry(5, EXPIRE)
    assert panel.writes == []


async def test_prod_refuses_mismatched_own_looking_account(
    panel: MockPanel, make_client: MakeClient
) -> None:
    panel.on("GET", "/api/users/by-username/tg_5", ok(user(username="tg_6")))
    with pytest.raises(RemnawaveResponseError):
        await make_client(RunMode.PROD).disable_account(5)
    assert panel.writes == []


async def test_prod_never_deletes(panel: MockPanel, make_client: MakeClient) -> None:
    with pytest.raises(OperationNotAllowed):
        await make_client(RunMode.PROD).delete_account(5)
    assert panel.requests == []


async def test_dev_reads_of_foreign_refused(panel: MockPanel, make_client: MakeClient) -> None:
    panel.on("GET", "/api/users/by-username/dev_5", ok(user(username="dev_5", squads=())))
    with pytest.raises(ForeignAccountError):
        await make_client(RunMode.DEV).get_account(5)


async def test_prod_list_hides_foreign(panel: MockPanel, make_client: MakeClient) -> None:
    users = [
        user(user_id=1, username="tg_1"),
        user(user_id=2, username="alex"),
        user(user_id=3, username="dev_3", squads=("test",)),
        user(user_id=4, username="tg_4x"),
    ]
    panel.on("GET", "/api/users/stream", ok({"users": users, "nextCursor": None, "hasMore": False}))
    accounts = await make_client(RunMode.PROD).list_accounts()
    assert [a.username for a in accounts] == ["tg_1"]


async def test_dev_list_hides_foreign(panel: MockPanel, make_client: MakeClient) -> None:
    users = [
        user(user_id=1, username="tg_1"),
        user(user_id=2, username="dev_2", squads=("test",)),
        user(user_id=3, username="dev_3", squads=("Default-Squad",)),
        user(user_id=4, username="maria", squads=("test",)),
    ]
    panel.on("GET", "/api/users/stream", ok({"users": users, "nextCursor": None, "hasMore": False}))
    accounts = await make_client(RunMode.DEV).list_accounts()
    assert [a.username for a in accounts] == ["dev_2"]


async def test_dev_existing_foreign_on_create_not_touched(
    panel: MockPanel, make_client: MakeClient
) -> None:
    panel.on("GET", "/api/users/by-username/dev_5", ok(user(username="dev_5", squads=())))
    with pytest.raises(ForeignAccountError):
        await make_client(RunMode.DEV).get_or_create_account(
            5, expire_at=EXPIRE, device_limit=3, traffic_limit_bytes=0
        )
    assert panel.writes == []


# --- Подделка применяет ту же защиту ---


@pytest.mark.parametrize("action", list(WRITE_ACTIONS))
async def test_fake_dev_refuses_foreign(action: str) -> None:
    fake = FakeRemnawave(RunMode.DEV)
    fake.add_foreign_account("dev_5", squad_names={"Default-Squad"})
    with pytest.raises(ForeignAccountError):
        await WRITE_ACTIONS[action](fake, 5)
    assert fake.writes == []


async def test_fake_lists_only_own() -> None:
    fake = FakeRemnawave(RunMode.PROD)
    fake.add_foreign_account("alex")
    fake.add_foreign_account("dev_1", squad_names={"test"})
    await fake.get_or_create_account(7, expire_at=EXPIRE, device_limit=3, traffic_limit_bytes=0)
    assert [a.username for a in await fake.list_accounts()] == ["tg_7"]
