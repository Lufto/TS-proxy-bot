"""Компенсация при сбое ноды (задача 20): один раз на инцидент и на клиента, только клиентам
бота с оплаченной подпиской, перенос в льготный период, продолжение после сбоя посреди."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest
from conftest import NOW
from signal_helpers import admin_texts, make_alerter, make_node
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, NodeOutage, Order
from tsproxy.domain.compensation import CompensationAction, Outage
from tsproxy.domain.constants import BYTES_PER_GB, PAID_DEVICE_LIMIT, UNLIMITED_TRAFFIC_BYTES
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    AccountStatus,
    ProxyAccount,
    RemnawaveUnavailable,
)
from tsproxy.services.compensation import (
    Apology,
    ClientOutcome,
    CompensationOffer,
    CompensationReport,
    CompensationRun,
    CompensationService,
    StartOutcome,
    carried_compensation,
)
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.nodes import NodeMonitor

ADMIN_ID = 900
ACTIVE_ID = 111  # клиент из фикстуры `client`
GRACE_ID = 222
TRIAL_ID = 333
DAY = timedelta(days=1)
# Сбой 2 ч 10 мин → 3 часа (вверх до часа) + 1 день
OUTAGE_START = NOW - timedelta(hours=3)
OUTAGE_END = OUTAGE_START + timedelta(hours=2, minutes=10)
AMOUNT = timedelta(hours=3) + DAY
ACTIVE_EXPIRE = NOW + timedelta(days=10)


class FlakyRemnawave(FakeRemnawave):
    """Панель, у которой «поставь срок» падает: до записи или после (таймаут ответа)."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(RunMode.PROD, now=clock)
        self.expiry_failures = 0
        self.applies_before_failing = False

    async def set_expiry(self, telegram_id: int, expire_at: datetime) -> ProxyAccount:
        if self.expiry_failures:
            self.expiry_failures -= 1
            if self.applies_before_failing:
                await super().set_expiry(telegram_id, expire_at)
            raise RemnawaveUnavailable("timeout")
        return await super().set_expiry(telegram_id, expire_at)


@dataclass
class FakeReporter:
    offers: list[CompensationOffer] = field(default_factory=list)
    started_runs: list[tuple[int, int]] = field(default_factory=list)
    progress_calls: list[tuple[int, int]] = field(default_factory=list)
    reports: list[CompensationReport] = field(default_factory=list)

    async def offer(self, offer: CompensationOffer) -> None:
        self.offers.append(offer)

    async def started(self, run: CompensationRun, total: int) -> None:
        self.started_runs.append((run.run_id, total))

    async def progress(self, run: CompensationRun, done: int, total: int) -> None:
        self.progress_calls.append((done, total))

    async def finished(self, report: CompensationReport) -> None:
        self.reports.append(report)


@dataclass
class FakeApologies:
    sent: list[tuple[int, Apology]] = field(default_factory=list)
    error: Exception | None = None

    async def send_apology(self, telegram_id: int, apology: Apology) -> bool:
        if self.error is not None:
            raise self.error
        self.sent.append((telegram_id, apology))
        return True


@dataclass
class Setup:
    service: CompensationService
    remnawave: FlakyRemnawave
    reporter: FakeReporter
    apologies: FakeApologies
    alerts: FakeAlertSender
    session_factory: async_sessionmaker[AsyncSession]
    clock: FakeClock
    term_locks: KeyedLocks


@pytest.fixture
def s(engine: AsyncEngine, clock: FakeClock, mask: Masker) -> Setup:
    session_factory = create_session_factory(engine)
    remnawave = FlakyRemnawave(clock)
    reporter = FakeReporter()
    apologies = FakeApologies()
    alerts = FakeAlertSender()
    term_locks = KeyedLocks()
    service = CompensationService(
        session_factory,
        remnawave,
        EventLog(clock, mask),
        make_alerter(alerts, clock, mask),
        admin_texts(),
        reporter,
        apologies,
        clock,
        term_locks,
        progress_every=2,
    )
    return Setup(
        service, remnawave, reporter, apologies, alerts, session_factory, clock, term_locks
    )


async def add_outage(
    s: Setup,
    started_at: datetime = OUTAGE_START,
    ended_at: datetime | None = OUTAGE_END,
    node_uuid: str = "node-1",
) -> int:
    async with s.session_factory() as session:
        outage = NodeOutage(node_uuid=node_uuid, started_at=started_at, ended_at=ended_at)
        session.add(outage)
        await session.commit()
        return outage.id


async def add_client(s: Setup, telegram_id: int, **fields: object) -> Client:
    async with s.session_factory() as session:
        client = Client(telegram_id=telegram_id, first_seen_at=NOW, **fields)
        session.add(client)
        await session.commit()
        return client


async def paid_account(s: Setup, telegram_id: int, expire_at: datetime) -> None:
    await s.remnawave.get_or_create_account(
        telegram_id,
        expire_at=expire_at,
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )


async def expire_of(s: Setup, telegram_id: int) -> datetime:
    return (await s.remnawave.get_account(telegram_id)).expire_at


async def grant_events(s: Setup) -> list[Event]:
    async with s.session_factory() as session:
        return list(
            await session.scalars(
                select(Event)
                .where(Event.kind == EventKind.COMPENSATION_GRANTED.value)
                .order_by(Event.id)
            )
        )


async def start(s: Setup, outage_id: int) -> CompensationRun:
    started = await s.service.compensate(outage_id, ADMIN_ID)
    assert started.run is not None
    return started.run


@pytest.fixture
async def active(s: Setup, client: Client) -> Client:
    await paid_account(s, ACTIVE_ID, ACTIVE_EXPIRE)
    return client


# --- Кому и сколько ---


async def test_active_bot_client_gets_outage_plus_day_and_apology(s: Setup, active: Client) -> None:
    outage_id = await add_outage(s)

    started = await s.service.compensate(outage_id, ADMIN_ID)
    assert started.outcome is StartOutcome.STARTED
    assert started.run is not None
    report = await s.service.run(started.run)

    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + AMOUNT
    assert report.counts[ClientOutcome.EXTENDED] == 1
    assert s.apologies.sent == [
        (ACTIVE_ID, Apology(CompensationAction.EXTEND, AMOUNT, ACTIVE_EXPIRE + AMOUNT))
    ]
    [event] = await grant_events(s)
    assert event.client_id == active.id
    assert event.admin_id == ADMIN_ID
    assert event.details is not None
    assert event.details["applied_at"] is not None
    assert s.reporter.started_runs == [(started.run.run_id, 1)]
    assert s.reporter.reports == [report]
    async with s.session_factory() as session:
        outage = await session.get_one(NodeOutage, outage_id)
        assert outage.compensated_at == NOW
        assert outage.compensated_by_admin_id == ADMIN_ID


async def test_manual_panel_clients_are_not_touched(s: Setup, active: Client) -> None:
    # аккаунт, заведённый вручную, — даже с тем же Telegram ID и сроком в будущем
    foreign = s.remnawave.add_foreign_account("ivan_manual", telegram_id=ACTIVE_ID)
    s.remnawave.accounts["ivan_manual"].expire_at = NOW + timedelta(days=20)
    run = await start(s, await add_outage(s))

    await s.service.run(run)

    stored = s.remnawave.accounts["ivan_manual"]
    assert stored.expire_at == NOW + timedelta(days=20)
    assert ("set_expiry", foreign.username) not in s.remnawave.writes
    assert [telegram_id for telegram_id, _ in s.apologies.sent] == [ACTIVE_ID]


async def test_trial_expired_and_disabled_clients_get_nothing(s: Setup) -> None:
    await add_client(s, TRIAL_ID, trial_granted_at=NOW - DAY)
    await s.remnawave.get_or_create_account(
        TRIAL_ID, expire_at=NOW + DAY, device_limit=3, traffic_limit_bytes=7 * BYTES_PER_GB
    )
    await add_client(s, 444)
    await paid_account(s, 444, NOW + DAY)
    s.remnawave.set_status(444, AccountStatus.DISABLED)
    run = await start(s, await add_outage(s))

    report = await s.service.run(run)

    assert report.counts[ClientOutcome.NOT_ELIGIBLE] == 2
    assert await expire_of(s, TRIAL_ID) == NOW + DAY
    assert s.apologies.sent == []
    assert await grant_events(s) == []


async def test_grace_client_keeps_term_and_gets_carry_over(s: Setup) -> None:
    grace_started = NOW - DAY
    grace_client = await add_client(s, GRACE_ID, grace_started_at=grace_started)
    grace_end = grace_started + timedelta(days=4)
    await paid_account(s, GRACE_ID, grace_end)
    run = await start(s, await add_outage(s))

    report = await s.service.run(run)

    assert report.counts[ClientOutcome.CARRIED_OVER] == 1
    assert await expire_of(s, GRACE_ID) == grace_end  # конец льготного не сдвигаем
    assert s.apologies.sent == [(GRACE_ID, Apology(CompensationAction.CARRY_OVER, AMOUNT))]
    async with s.session_factory() as session:
        assert await carried_compensation(session, grace_client.id, grace_started) == AMOUNT


async def test_carried_compensation_counts_only_current_grace(s: Setup) -> None:
    grace_client = await add_client(s, GRACE_ID, grace_started_at=NOW - DAY)
    await paid_account(s, GRACE_ID, NOW + 3 * DAY)
    await s.service.run(await start(s, await add_outage(s)))

    async with s.session_factory() as session:
        # следующий льготный период начнётся позже — старый перенос в нём не считается
        assert await carried_compensation(session, grace_client.id, NOW + DAY) == timedelta(0)
        assert await carried_compensation(session, grace_client.id, None) == timedelta(0)


async def test_blocked_client_is_compensated_without_message(s: Setup) -> None:
    await add_client(s, GRACE_ID, bot_blocked_at=NOW - DAY)
    await paid_account(s, GRACE_ID, ACTIVE_EXPIRE)
    await s.service.run(await start(s, await add_outage(s)))

    assert await expire_of(s, GRACE_ID) == ACTIVE_EXPIRE + AMOUNT
    assert s.apologies.sent == []


async def test_failed_apology_does_not_stop_the_run(s: Setup, active: Client) -> None:
    await add_client(s, GRACE_ID)
    await paid_account(s, GRACE_ID, ACTIVE_EXPIRE)
    s.apologies.error = RuntimeError("telegram down")

    report = await s.service.run(await start(s, await add_outage(s)))

    assert report.counts[ClientOutcome.EXTENDED] == 2


async def test_bot_account_without_client_row_is_skipped(s: Setup) -> None:
    await paid_account(s, 555, ACTIVE_EXPIRE)
    report = await s.service.run(await start(s, await add_outage(s)))
    assert report.counts[ClientOutcome.SKIPPED] == 1
    assert await expire_of(s, 555) == ACTIVE_EXPIRE


async def test_client_whose_payment_is_being_granted_is_deferred(s: Setup, active: Client) -> None:
    async with s.session_factory() as session:
        session.add(
            Order(
                client_id=active.id,
                tariff_id="m1",
                price=150,
                days=30,
                status=OrderStatus.PAID,
                created_at=NOW,
            )
        )
        await session.commit()
    run = await start(s, await add_outage(s))

    report = await s.service.run(run)

    assert report.counts[ClientOutcome.DEFERRED] == 1
    assert report.unfinished == 1
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE


# --- Один раз на инцидент и на клиента ---


async def test_double_press_does_not_compensate_twice(s: Setup, active: Client) -> None:
    outage_id = await add_outage(s)

    first = await s.service.compensate(outage_id, ADMIN_ID)
    second = await s.service.compensate(outage_id, ADMIN_ID)
    assert first.outcome is StartOutcome.STARTED
    assert second.outcome is StartOutcome.RESUMED
    assert first.run is not None
    assert second.run is not None
    assert second.run.run_id == first.run.run_id
    assert second.run.amount == AMOUNT
    await s.service.run(first.run)
    report = await s.service.run(second.run)

    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + AMOUNT
    assert report.counts[ClientOutcome.ALREADY_COMPENSATED] == 1
    assert len(await grant_events(s)) == 1
    assert len(s.apologies.sent) == 1


async def test_simultaneous_presses_compensate_once(s: Setup, active: Client) -> None:
    outage_id = await add_outage(s)

    starts = await asyncio.gather(
        s.service.compensate(outage_id, ADMIN_ID), s.service.compensate(outage_id, ADMIN_ID)
    )
    runs = [started.run for started in starts if started.run is not None]
    await asyncio.gather(*(s.service.run(run) for run in runs))

    assert sorted(started.outcome for started in starts) == [
        StartOutcome.RESUMED,
        StartOutcome.STARTED,
    ]
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + AMOUNT
    assert len(await grant_events(s)) == 1


async def test_launch_refuses_second_background_run(s: Setup, active: Client) -> None:
    run = await start(s, await add_outage(s))

    task = s.service.launch(run)
    assert task is not None
    assert s.service.launch(run) is None  # уже идёт
    await task

    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + AMOUNT
    again = s.service.launch(run)  # закончился — можно снова, начислений не будет
    assert again is not None
    await again
    assert len(await grant_events(s)) == 1


async def test_background_failure_raises_alert(s: Setup, active: Client) -> None:
    run = await start(s, await add_outage(s))
    s.remnawave.fail_next(RemnawaveUnavailable("panel down"))  # список аккаунтов не прочитан

    task = s.service.launch(run)
    assert task is not None
    await task

    [alert] = s.alerts.sent
    assert f"#{run.run_id}" in alert.text
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE


async def test_unknown_or_open_outage_is_not_compensated(s: Setup) -> None:
    open_id = await add_outage(s, ended_at=None)
    assert (await s.service.compensate(open_id, ADMIN_ID)).outcome is StartOutcome.STILL_OPEN
    assert (await s.service.compensate(9999, ADMIN_ID)).outcome is StartOutcome.NOT_FOUND
    assert await s.service.describe(open_id) is None


# --- Продолжение после сбоя посреди прогона ---


async def test_panel_error_midway_is_finished_by_next_press(s: Setup, active: Client) -> None:
    await add_client(s, GRACE_ID)
    await paid_account(s, GRACE_ID, ACTIVE_EXPIRE)
    s.remnawave.expiry_failures = 1  # у первого клиента срок не поставился
    outage_id = await add_outage(s)
    run = await start(s, outage_id)

    first = await s.service.run(run)
    assert first.counts[ClientOutcome.FAILED] == 1
    assert first.counts[ClientOutcome.EXTENDED] == 1
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE

    resumed = await s.service.compensate(outage_id, ADMIN_ID)
    assert resumed.run is not None
    second = await s.service.run(resumed.run)

    assert second.counts[ClientOutcome.EXTENDED] == 1
    assert second.counts[ClientOutcome.ALREADY_COMPENSATED] == 1
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + AMOUNT
    assert await expire_of(s, GRACE_ID) == ACTIVE_EXPIRE + AMOUNT
    assert len(s.apologies.sent) == 2


async def test_lost_panel_answer_is_not_applied_twice(s: Setup, active: Client) -> None:
    s.remnawave.expiry_failures = 1
    s.remnawave.applies_before_failing = True  # панель срок поставила, ответ потерялся
    run = await start(s, await add_outage(s))

    assert (await s.service.run(run)).counts[ClientOutcome.FAILED] == 1
    report = await s.service.run(run)

    assert report.counts[ClientOutcome.EXTENDED] == 1
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + AMOUNT
    assert len(s.apologies.sent) == 1
    assert (await s.service.run(run)).counts[ClientOutcome.ALREADY_COMPENSATED] == 1


async def test_term_changed_after_failed_extension_goes_to_manual_check(
    s: Setup, active: Client
) -> None:
    s.remnawave.expiry_failures = 1
    run = await start(s, await add_outage(s))
    await s.service.run(run)
    await s.remnawave.set_expiry(ACTIVE_ID, ACTIVE_EXPIRE + timedelta(days=30))  # оплата

    report = await s.service.run(run)

    assert report.counts[ClientOutcome.MANUAL] == 1
    assert report.manual == (ACTIVE_ID,)
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + timedelta(days=30)


async def test_compensation_waits_for_term_lock(s: Setup, active: Client) -> None:
    run = await start(s, await add_outage(s))
    async with s.term_locks.hold(active.id):
        task = asyncio.create_task(s.service.run(run))
        await asyncio.sleep(0.05)
        # пока срок держит, например, зачёт оплаты — компенсация не трогает панель
        await s.remnawave.set_expiry(ACTIVE_ID, ACTIVE_EXPIRE + timedelta(days=30))
    await task
    # компенсация прочитала срок уже после оплаты — дни не потерялись
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + timedelta(days=30) + AMOUNT


async def test_progress_is_reported(s: Setup, active: Client) -> None:
    for telegram_id in (601, 602, 603):
        await add_client(s, telegram_id)
        await paid_account(s, telegram_id, ACTIVE_EXPIRE)

    await s.service.run(await start(s, await add_outage(s)))

    assert s.reporter.progress_calls == [(2, 4)]
    assert s.reporter.reports[0].total == 4


# --- «Мигание» ---


async def test_blink_after_compensation_adds_only_new_downtime(s: Setup, active: Client) -> None:
    first_id = await add_outage(s, OUTAGE_START, OUTAGE_START + timedelta(hours=1))
    await s.service.run(await start(s, first_id))
    after_first = await expire_of(s, ACTIVE_ID)
    assert after_first == ACTIVE_EXPIRE + timedelta(hours=1) + DAY
    # через 20 минут нода снова упала на полчаса — тот же инцидент
    second_start = OUTAGE_START + timedelta(hours=1, minutes=20)
    second_id = await add_outage(s, second_start, second_start + timedelta(minutes=30))

    offer = await s.service.describe(second_id)
    assert offer is not None
    assert offer.incident == Outage(OUTAGE_START, second_start + timedelta(minutes=30))
    assert offer.amount == timedelta(hours=1)
    second = await s.service.compensate(second_id, ADMIN_ID)
    assert second.outcome is StartOutcome.STARTED
    assert second.run is not None
    await s.service.run(second.run)

    assert await expire_of(s, ACTIVE_ID) == after_first + timedelta(hours=1)
    # первое нажатие снова — продолжает свой прогон, ничего не добавляет
    again = await s.service.compensate(first_id, ADMIN_ID)
    assert again.outcome is StartOutcome.RESUMED
    assert again.run is not None
    await s.service.run(again.run)
    assert await expire_of(s, ACTIVE_ID) == after_first + timedelta(hours=1)


async def test_outage_inside_compensated_one_gives_nothing(s: Setup, active: Client) -> None:
    first_id = await add_outage(s)
    await s.service.run(await start(s, first_id))
    inner_id = await add_outage(
        s, OUTAGE_START + timedelta(minutes=10), OUTAGE_START + timedelta(hours=1), "node-2"
    )

    await s.service.outage_closed(inner_id)  # предлагать нечего
    run = await start(s, inner_id)
    report = await s.service.run(run)

    assert s.reporter.offers == []
    assert run.amount == timedelta(0)
    assert report.total == 0
    assert await expire_of(s, ACTIVE_ID) == ACTIVE_EXPIRE + AMOUNT


# --- Предложение после восстановления ноды ---


async def test_restored_node_offers_compensation(s: Setup, mask: Masker, clock: FakeClock) -> None:
    alerts = FakeAlertSender()
    monitor = NodeMonitor(
        s.remnawave,
        s.session_factory,
        make_alerter(alerts, clock, mask),
        admin_texts(),
        clock,
        on_closed=s.service,
    )
    s.remnawave.nodes = [make_node(connected=False, changed_at=OUTAGE_START)]
    await monitor.refresh()
    s.remnawave.nodes = [make_node(connected=True, changed_at=OUTAGE_END)]

    await monitor.refresh()

    [offer] = s.reporter.offers
    assert offer.incident == Outage(OUTAGE_START, OUTAGE_END)
    assert offer.amount == AMOUNT
    assert len(alerts.sent) == 2  # «потеряла связь» и «восстановлена» — как раньше
