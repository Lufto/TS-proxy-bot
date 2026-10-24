"""Сверка с Remnawave: находит то, что пропустили вебхуки, и сама не падает."""

from datetime import timedelta

import pytest
from conftest import NOW
from signal_helpers import (
    NODE_UUID,
    RecordingHandler,
    admin_texts,
    create_account,
    make_alerter,
    make_node,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import NodeOutage
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    AccountStatus,
    RemnawaveAuthError,
    RemnawaveUnavailable,
)
from tsproxy.services.account_checks import AccountChecks, CheckSource
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.nodes import NodeMonitor
from tsproxy.services.reconciliation import Reconciliation

CLIENT_ID = 111


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def handler() -> RecordingHandler:
    return RecordingHandler()


@pytest.fixture
def reconciliation(
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    handler: RecordingHandler,
    clock: FakeClock,
    mask: Masker,
) -> Reconciliation:
    alerter = make_alerter(sender, clock, mask)
    texts = admin_texts()
    return Reconciliation(
        AccountChecks(remnawave, RunMode.PROD, clock, [handler]),
        NodeMonitor(remnawave, session_factory, alerter, texts, clock),
        alerter,
        texts,
    )


async def test_expiry_missed_while_bot_was_down_is_found(
    reconciliation: Reconciliation,
    remnawave: FakeRemnawave,
    handler: RecordingHandler,
    clock: FakeClock,
) -> None:
    """Готово, когда (задача 09): событие, пришедшее, пока бот лежал, найдено сверкой."""
    await create_account(remnawave, CLIENT_ID)
    clock.advance(timedelta(days=7, minutes=1))
    remnawave.set_status(CLIENT_ID, AccountStatus.EXPIRED)  # `user.expired` ушёл в пустоту

    await reconciliation.run()

    [check] = handler.checks
    assert check.telegram_id == CLIENT_ID
    assert check.source is CheckSource.RECONCILIATION
    assert check.account.status is AccountStatus.EXPIRED
    assert check.account.expire_at <= check.checked_at


async def test_node_outage_missed_by_webhook_is_found(
    reconciliation: Reconciliation,
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    remnawave.nodes = [make_node(connected=False, changed_at=NOW - timedelta(minutes=7))]

    await reconciliation.run()

    async with session_factory() as session:
        [outage] = (await session.scalars(select(NodeOutage))).all()
    assert outage.node_uuid == NODE_UUID
    assert ["потеряла связь" in m.text for m in sender.sent] == [True]


async def test_panel_unavailable_raises_alert_and_does_not_crash(
    reconciliation: Reconciliation,
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    handler: RecordingHandler,
) -> None:
    await create_account(remnawave, CLIENT_ID)
    remnawave.fail_next(RemnawaveUnavailable("нет связи"))

    await reconciliation.run()

    assert handler.checks == []
    [alert] = sender.sent
    assert "панель не отвечает" in alert.text


async def test_other_panel_error_raises_alert(
    reconciliation: Reconciliation, remnawave: FakeRemnawave, sender: FakeAlertSender
) -> None:
    remnawave.fail_next(RemnawaveAuthError("HTTP 401"))

    await reconciliation.run()

    [alert] = sender.sent
    assert "RemnawaveAuthError" in alert.text


async def test_handler_failures_raise_one_alert(
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    clock: FakeClock,
    mask: Masker,
) -> None:
    alerter = make_alerter(sender, clock, mask)
    texts = admin_texts()
    broken = RecordingHandler(error=RuntimeError("сломался"))
    reconciliation = Reconciliation(
        AccountChecks(remnawave, RunMode.PROD, clock, [broken]),
        NodeMonitor(remnawave, session_factory, alerter, texts, clock),
        alerter,
        texts,
    )
    await create_account(remnawave, CLIENT_ID)
    await create_account(remnawave, CLIENT_ID + 1)

    await reconciliation.run()

    [alert] = sender.sent
    assert "— 2." in alert.text


async def test_quiet_when_everything_is_fine(
    reconciliation: Reconciliation, remnawave: FakeRemnawave, sender: FakeAlertSender
) -> None:
    await create_account(remnawave, CLIENT_ID)
    remnawave.nodes = [make_node(connected=True, changed_at=NOW - timedelta(days=1))]

    await reconciliation.run()

    assert sender.sent == []
