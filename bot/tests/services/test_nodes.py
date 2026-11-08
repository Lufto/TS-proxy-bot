"""Связь нод с панелью: тревога о потере связи и о восстановлении — один раз на сбой."""

from datetime import timedelta

import pytest
from conftest import NOW
from signal_helpers import NODE_UUID, admin_texts, make_alerter, make_node
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import NodeOutage
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.nodes import NodeMonitor

DOWN_AT = NOW - timedelta(minutes=3)


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def sender() -> FakeAlertSender:
    return FakeAlertSender()


def make_monitor(
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    clock: FakeClock,
    mask: Masker,
) -> NodeMonitor:
    return NodeMonitor(
        remnawave, session_factory, make_alerter(sender, clock, mask), admin_texts(), clock
    )


@pytest.fixture
def monitor(
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    sender: FakeAlertSender,
    clock: FakeClock,
    mask: Masker,
) -> NodeMonitor:
    return make_monitor(remnawave, session_factory, sender, clock, mask)


async def outages(session_factory: async_sessionmaker[AsyncSession]) -> list[NodeOutage]:
    async with session_factory() as session:
        return list((await session.scalars(select(NodeOutage).order_by(NodeOutage.id))).all())


async def test_lost_connection_opens_outage_and_alerts_once(
    monitor: NodeMonitor,
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    remnawave.nodes = [make_node(connected=False, changed_at=DOWN_AT)]

    await monitor.refresh()
    clock.advance(timedelta(minutes=5))
    await monitor.refresh()  # сверка видит ту же упавшую ноду
    await monitor.refresh()  # и повторный вебхук

    [outage] = await outages(session_factory)
    assert outage.node_uuid == NODE_UUID
    assert outage.started_at == DOWN_AT  # время из панели, а не время проверки
    assert outage.ended_at is None
    [alert] = sender.sent
    assert "Нидерланды-1" in alert.text
    assert "потеряла связь" in alert.text
    assert "24.10.2026, 14:57 МСК" in alert.text


async def test_restored_connection_closes_outage_with_duration(
    monitor: NodeMonitor,
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    remnawave.nodes = [make_node(connected=False, changed_at=DOWN_AT)]
    await monitor.refresh()
    clock.advance(timedelta(minutes=40))
    up_at = clock.now - timedelta(minutes=2)
    remnawave.nodes = [make_node(connected=True, changed_at=up_at)]

    await monitor.refresh()
    await monitor.refresh()  # повтор не даёт второй тревоги

    [outage] = await outages(session_factory)
    assert outage.ended_at == up_at
    assert outage.compensated_at is None  # компенсация — решение администратора (задача 20)
    assert len(sender.sent) == 2
    restored = sender.sent[1].text
    assert "восстановлена" in restored
    assert "41 мин" in restored  # с 11:57 до 12:38 UTC


async def test_connected_node_without_outage_is_quiet(
    monitor: NodeMonitor,
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    remnawave.nodes = [make_node(connected=True, changed_at=DOWN_AT)]

    await monitor.refresh()

    assert sender.sent == []
    assert await outages(session_factory) == []


async def test_disabled_node_is_not_an_outage(
    monitor: NodeMonitor,
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Администратор сам выключил ноду в панели — это не сбой."""
    remnawave.nodes = [make_node(connected=False, changed_at=DOWN_AT, disabled=True)]

    await monitor.refresh()

    assert sender.sent == []
    assert await outages(session_factory) == []


async def test_restart_does_not_repeat_alert(
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
) -> None:
    remnawave.nodes = [make_node(connected=False, changed_at=DOWN_AT)]
    await make_monitor(remnawave, session_factory, sender, clock, mask).refresh()

    # бот перезапустился — память пустая, но сбой записан в базе
    await make_monitor(remnawave, session_factory, sender, clock, mask).refresh()

    assert len(sender.sent) == 1
    assert len(await outages(session_factory)) == 1


async def test_outage_missed_while_bot_was_down(
    monitor: NodeMonitor,
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    """Нода упала и поднялась, пока бот лежал, — вебхуков не было. Сверка видит уже
    восстановленную ноду и не знает о простое: такой сбой не записываем (см. отчёт)."""
    remnawave.nodes = [make_node(connected=True, changed_at=clock.now - timedelta(minutes=1))]

    await monitor.refresh()

    assert sender.sent == []
    assert await outages(session_factory) == []


async def test_unknown_change_time_uses_now(
    monitor: NodeMonitor,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
) -> None:
    remnawave.nodes = [make_node(connected=False, changed_at=None)]
    await monitor.refresh()
    clock.advance(timedelta(minutes=10))
    # часы панели убежали вперёд: время восстановления позже «сейчас» — берём «сейчас»
    remnawave.nodes = [make_node(connected=True, changed_at=clock.now + timedelta(hours=1))]
    await monitor.refresh()

    [outage] = await outages(session_factory)
    assert outage.started_at == NOW
    assert outage.ended_at == clock.now


async def test_several_nodes_are_independent(
    monitor: NodeMonitor,
    remnawave: FakeRemnawave,
    sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    remnawave.nodes = [
        make_node(connected=False, changed_at=DOWN_AT, uuid="a", name="Нода А"),
        make_node(connected=True, changed_at=DOWN_AT, uuid="b", name="Нода Б"),
        make_node(connected=False, changed_at=DOWN_AT, uuid="c", name="Нода В"),
    ]

    await monitor.refresh()

    assert sorted(o.node_uuid for o in await outages(session_factory)) == ["a", "c"]
    assert len(sender.sent) == 2


async def test_panel_unavailable_is_raised(monitor: NodeMonitor, remnawave: FakeRemnawave) -> None:
    remnawave.fail_next(RemnawaveUnavailable("нет связи"))

    with pytest.raises(RemnawaveUnavailable):
        await monitor.refresh()
