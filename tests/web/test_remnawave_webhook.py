"""Вебхук Remnawave (аудит §4.2): поддельный отклонён, настоящий — только сигнал."""

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.content.loader import load_content
from tsproxy.db.engine import create_engine, create_session_factory
from tsproxy.db.models import Base, NodeOutage
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus, Node, RemnawaveUnavailable
from tsproxy.services.account_checks import AccountCheck, AccountChecks, CheckSource
from tsproxy.services.alerts import Alerter
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.nodes import NodeMonitor
from tsproxy.services.remnawave_signals import RemnawaveSignals
from tsproxy.web.app import create_web_app
from tsproxy.web.remnawave import (
    MAX_BODY_BYTES,
    REMNAWAVE_WEBHOOK_PATH,
    SIGNATURE_HEADER,
    RemnawaveWebhook,
    signature_valid,
)

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
SECRET = "Rw" + "0123456789abcdef" * 2
CLIENT_ID = 111
NODE_UUID = "4c1a6c7e-0000-4000-8000-000000000001"


@dataclass
class RecordingHandler:
    checks: list[AccountCheck] = field(default_factory=list)

    async def handle(self, check: AccountCheck) -> None:
        self.checks.append(check)


def user_event(event: str, username: str, *, at: datetime = NOW) -> bytes:
    """Тело вебхука так, как его собирает панель 3.4.5 (JSON.stringify, без пробелов)."""
    payload: dict[str, Any] = {
        "scope": "user",
        "event": event,
        "timestamp": at.isoformat().replace("+00:00", "Z"),
        "data": {
            "uuid": "0b6c7b38-6a3e-4c0e-9a1c-3c1f1b2d3e4f",
            "id": 7,
            "shortUuid": "AbCdEfGh12345678",
            "username": username,
            "status": "EXPIRED",
            "expireAt": "2026-10-24T11:59:00.000Z",
            "telegramId": CLIENT_ID,
            "subscriptionUrl": "https://sub.ts-proxy.com/AbCdEfGh12345678",
        },
        "meta": None,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def hwid_event(username: str) -> bytes:
    payload = {
        "scope": "user_hwid_devices",
        "event": "user_hwid_devices.added",
        "timestamp": NOW.isoformat(),
        "data": {"user": {"username": username}, "hwidUserDevice": {"hwid": "abc"}},
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def node_event(event: str) -> bytes:
    payload = {
        "scope": "node",
        "event": event,
        "timestamp": NOW.isoformat(),
        "data": {"uuid": NODE_UUID, "name": "Нидерланды-1", "isConnected": False},
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@dataclass
class Env:
    http: httpx.AsyncClient
    remnawave: FakeRemnawave
    handler: RecordingHandler
    sender: FakeAlertSender
    clock: FakeClock
    session_factory: async_sessionmaker[AsyncSession]

    async def post(self, body: bytes, signature: str | bytes | None) -> httpx.Response:
        headers: dict[str, str | bytes] = {"Content-Type": "application/json"}
        if signature is not None:
            headers[SIGNATURE_HEADER] = signature
        return await self.http.post(REMNAWAVE_WEBHOOK_PATH, content=body, headers=headers)

    async def create_account(self) -> None:
        await self.remnawave.get_or_create_account(
            CLIENT_ID, expire_at=NOW + timedelta(days=7), device_limit=3, traffic_limit_bytes=0
        )


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def env(engine: AsyncEngine) -> AsyncIterator[Env]:
    clock = FakeClock(NOW)
    remnawave = FakeRemnawave(RunMode.PROD, now=clock)
    handler = RecordingHandler()
    sender = FakeAlertSender()
    texts = load_content(REPO_CONTENT).admin_texts
    alerter = Alerter(sender, texts, -100123, 2, clock, lambda text: text)
    session_factory = create_session_factory(engine)
    signals = RemnawaveSignals(
        AccountChecks(remnawave, RunMode.PROD, clock, [handler]),
        NodeMonitor(remnawave, session_factory, alerter, texts, clock),
    )
    app = create_web_app(None, RemnawaveWebhook(signals, SECRET, alerter, texts, clock))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as http:
        yield Env(http, remnawave, handler, sender, clock, session_factory)


# --- Поддельные вебхуки ---


@pytest.mark.parametrize(
    "make_signature",
    [
        lambda body: None,
        lambda body: "",
        lambda body: "deadbeef",
        lambda body: sign(body, secret="другой-секрет-панели-0123456789ab"),
        lambda body: sign(body + b" "),  # подпись от другого тела
        lambda body: sign(body)[:-1],
        lambda body: sign(body).upper(),
        lambda body: "подпись".encode(),  # не ASCII не роняет сравнение
    ],
    ids=["none", "empty", "garbage", "other-secret", "other-body", "prefix", "upper", "unicode"],
)
async def test_forged_webhook_is_rejected(env: Env, make_signature: Any) -> None:
    """Готово, когда (задача 09): поддельный вебхук отклонён."""
    await env.create_account()
    body = user_event("user.expired", f"tg_{CLIENT_ID}")

    response = await env.post(body, make_signature(body))

    assert response.status_code == httpx.codes.FORBIDDEN
    assert env.handler.checks == []
    [alert] = env.sender.sent
    assert "неверной подписью" in alert.text


async def test_signature_is_over_raw_bytes_not_reparsed_json(env: Env) -> None:
    """Тот же JSON с другими пробелами — другая подпись: проверяем байты, а не смысл."""
    await env.create_account()
    body = user_event("user.expired", f"tg_{CLIENT_ID}")
    reformatted = json.dumps(json.loads(body), indent=2).encode()

    response = await env.post(reformatted, sign(body))

    assert response.status_code == httpx.codes.FORBIDDEN
    assert env.handler.checks == []


async def test_forged_alerts_are_glued(env: Env) -> None:
    body = user_event("user.expired", f"tg_{CLIENT_ID}")
    for _ in range(20):
        await env.post(body, "deadbeef")
    assert len(env.sender.sent) == 1


async def test_oversized_body_is_rejected(env: Env) -> None:
    body = b"{" + b" " * MAX_BODY_BYTES + b"}"

    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.REQUEST_ENTITY_TOO_LARGE
    assert env.handler.checks == []


# --- Настоящие вебхуки: только сигнал ---


async def test_signed_webhook_rereads_account(env: Env) -> None:
    await env.create_account()
    env.remnawave.set_status(CLIENT_ID, AccountStatus.EXPIRED)
    body = user_event("user.expired", f"tg_{CLIENT_ID}")

    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.OK
    [check] = env.handler.checks
    assert check.telegram_id == CLIENT_ID
    assert check.source is CheckSource.WEBHOOK
    assert check.event == "user.expired"
    # срок — из панели (7 дней), а не из тела вебхука (11:59)
    assert check.account.expire_at == NOW + timedelta(days=7)
    assert env.sender.sent == []


async def test_hwid_event_is_a_signal_about_its_account(env: Env) -> None:
    await env.create_account()
    body = hwid_event(f"tg_{CLIENT_ID}")

    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.OK
    assert [c.event for c in env.handler.checks] == ["user_hwid_devices.added"]


@pytest.mark.parametrize("username", ["ivan_petrov", "dev_111", "tg_111_old"])
async def test_webhook_about_foreign_account_is_ignored(env: Env, username: str) -> None:
    env.remnawave.add_foreign_account(username, telegram_id=CLIENT_ID)
    body = user_event("user.expired", username)

    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.OK
    assert env.handler.checks == []
    assert env.remnawave.writes == []


async def test_stale_webhook_is_ignored(env: Env) -> None:
    await env.create_account()
    body = user_event("user.expired", f"tg_{CLIENT_ID}", at=NOW - timedelta(minutes=11))

    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.OK
    assert env.handler.checks == []


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"event":"user.expired"}',
        b'{"event":"user.expired","timestamp":"2026-10-24T12:00:00Z","data":{}}',
        b'{"event":"user.expired","timestamp":"2026-10-24T12:00:00","data":{"username":"tg_1"}}',
        b"\xff\xfe",
    ],
    ids=["text", "list", "no-timestamp", "no-username", "naive-time", "bad-encoding"],
)
async def test_signed_but_unparsable_body_is_accepted_and_ignored(env: Env, body: bytes) -> None:
    """Подпись верна — отвечаем 200, иначе панель будет повторять; в лог пишем."""
    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.OK
    assert env.handler.checks == []


async def test_unneeded_event_is_ignored(env: Env) -> None:
    body = json.dumps(
        {
            "scope": "service",
            "event": "service.login_attempt_failed",
            "timestamp": NOW.isoformat(),
            "data": {"username": "admin", "ip": "1.2.3.4"},
        }
    ).encode()

    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.OK
    assert env.handler.checks == []


async def test_panel_unavailable_on_reread_still_answers_ok(env: Env) -> None:
    """Панель не ответила на перечитывание — 200, событие догонит сверка."""
    await env.create_account()
    env.remnawave.fail_next(RemnawaveUnavailable("нет связи"))
    body = user_event("user.expired", f"tg_{CLIENT_ID}")

    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.OK
    assert env.handler.checks == []


async def test_node_event_rereads_nodes(env: Env) -> None:
    env.remnawave.nodes = [
        Node(
            uuid=NODE_UUID,
            name="Нидерланды-1",
            is_connected=False,
            is_disabled=False,
            last_status_change=NOW - timedelta(seconds=30),
            last_status_message="connect ECONNREFUSED",
            users_online=None,
        )
    ]
    body = node_event("node.connection_lost")

    response = await env.post(body, sign(body))

    assert response.status_code == httpx.codes.OK
    async with env.session_factory() as session:
        [outage] = (await session.scalars(select(NodeOutage))).all()
    assert outage.node_uuid == NODE_UUID
    [alert] = env.sender.sent
    assert "потеряла связь" in alert.text


async def test_no_route_without_secret() -> None:
    app = create_web_app(None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as http:
        body = user_event("user.expired", f"tg_{CLIENT_ID}")
        response = await http.post(
            REMNAWAVE_WEBHOOK_PATH, content=body, headers={SIGNATURE_HEADER: sign(body)}
        )
    assert response.status_code == httpx.codes.NOT_FOUND


def test_signature_matches_remnawave_sdk() -> None:
    """Наша проверка совпадает с проверкой из SDK `remnawave-api` 3.4.5."""
    from remnawave.controllers.webhooks import WebhookUtility

    body = user_event("user.expired", f"tg_{CLIENT_ID}")
    signature = sign(body)

    assert WebhookUtility.validate_webhook(body.decode(), signature, SECRET)
    assert signature_valid(body, signature, SECRET)
    assert not signature_valid(body, signature, SECRET + "x")
    assert not signature_valid(body, "подпись", SECRET)
