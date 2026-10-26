"""Рассылка (задача 19): только клиентам бота из базы и без заблокировавших, один раз
на предпросмотр, с паузами между сообщениями и повтором после «подождите» от Telegram."""

import asyncio
from dataclasses import dataclass, field

import pytest
from conftest import NOW
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Client, Event
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.services.broadcast import (
    BROADCAST_SEND_INTERVAL,
    MAX_CONSECUTIVE_FAILURES,
    PROGRESS_EVERY,
    BroadcastEnd,
    BroadcastProgress,
    BroadcastReport,
    BroadcastService,
    BroadcastThrottled,
    DeliveryOutcome,
    PreviewRef,
    StartOutcome,
)
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker

ADMIN_ID = 900
ADMIN_CHAT_ID = -100500
PREVIEW = PreviewRef(chat_id=ADMIN_CHAT_ID, message_id=42)
TEXT = "<b>Новость</b>: тарифы & скидки"


@dataclass
class FakeSender:
    """Запоминает, кому что ушло. `outcomes` — что ответить конкретному клиенту."""

    sent: list[tuple[int, str]] = field(default_factory=list)
    outcomes: dict[int, DeliveryOutcome] = field(default_factory=dict)
    throttle: dict[int, list[float]] = field(default_factory=dict)  # Telegram: «подождите N с»
    gate: asyncio.Event | None = None  # пока не открыт — отправка ждёт (рассылка «идёт»)

    async def send(self, telegram_id: int, text: str) -> DeliveryOutcome:
        if self.gate is not None:
            await self.gate.wait()
        waits = self.throttle.get(telegram_id)
        if waits:
            raise BroadcastThrottled(waits.pop(0))
        self.sent.append((telegram_id, text))
        return self.outcomes.get(telegram_id, DeliveryOutcome.DELIVERED)

    @property
    def recipients(self) -> list[int]:
        return [telegram_id for telegram_id, _ in self.sent]


@dataclass
class FakeReporter:
    started_with: list[int] = field(default_factory=list)
    progress_reports: list[BroadcastProgress] = field(default_factory=list)
    reports: list[BroadcastReport] = field(default_factory=list)

    async def started(self, total: int) -> None:
        self.started_with.append(total)

    async def progress(self, progress: BroadcastProgress) -> None:
        self.progress_reports.append(progress)

    async def finished(self, report: BroadcastReport) -> None:
        self.reports.append(report)


@dataclass
class FakeSleep:
    waits: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        await asyncio.sleep(0)  # отпустить остальных, как настоящая пауза


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def reporter() -> FakeReporter:
    return FakeReporter()


@pytest.fixture
def sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
def broadcasts(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    sender: FakeSender,
    sleep: FakeSleep,
) -> BroadcastService:
    return BroadcastService(session_factory, EventLog(clock, mask), clock, sender, sleep=sleep)


async def add_clients(session: AsyncSession, *telegram_ids: int, blocked: bool = False) -> None:
    for telegram_id in telegram_ids:
        session.add(Client(telegram_id=telegram_id, bot_blocked_at=NOW if blocked else None))
    await session.commit()


async def broadcast_events(session: AsyncSession) -> list[Event]:
    query = select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value).order_by(Event.id)
    return list(await session.scalars(query))


async def run(broadcasts: BroadcastService, reporter: FakeReporter) -> BroadcastReport:
    started = await broadcasts.start(ADMIN_ID, PREVIEW, TEXT, reporter)
    assert started.outcome is StartOutcome.STARTED
    await broadcasts.wait()
    [report] = reporter.reports
    return report


# --- Кому уходит ---


async def test_goes_to_bot_clients_except_blocked_and_never_to_panel_clients(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
    clock: FakeClock,
) -> None:
    await add_clients(session, 111, 222)
    await add_clients(session, 333, blocked=True)
    # нынешние клиенты панели, заведённые вручную: в базе бота их нет
    panel = FakeRemnawave(RunMode.PROD, now=clock)
    manual = [panel.add_foreign_account(f"manual_{n}", telegram_id=500 + n) for n in range(28)]

    assert (await broadcasts.audience()).recipients == 2
    report = await run(broadcasts, reporter)

    assert sender.sent == [(111, TEXT), (222, TEXT)]  # текст как в предпросмотре, без экранирования
    assert not {account.telegram_id for account in manual} & set(sender.recipients)
    assert report.end is BroadcastEnd.COMPLETED
    assert report.progress == BroadcastProgress(total=2, delivered=2, blocked=0, failed=0)
    assert reporter.started_with == [2]
    assert panel.writes == []


async def test_audience_counts_recipients_and_blocked(
    broadcasts: BroadcastService, session: AsyncSession
) -> None:
    await add_clients(session, 111)
    await add_clients(session, 222, 333, blocked=True)

    audience = await broadcasts.audience()

    assert (audience.recipients, audience.blocked) == (1, 2)


async def test_nobody_to_send_to(
    broadcasts: BroadcastService, session: AsyncSession, reporter: FakeReporter
) -> None:
    await add_clients(session, 111, blocked=True)

    started = await broadcasts.start(ADMIN_ID, PREVIEW, TEXT, reporter)

    assert started.outcome is StartOutcome.NO_RECIPIENTS
    assert await broadcast_events(session) == []  # предпросмотр можно отправить позже


# --- Один раз на предпросмотр ---


async def test_start_is_logged_with_admin_and_text(
    broadcasts: BroadcastService, session: AsyncSession, reporter: FakeReporter
) -> None:
    await add_clients(session, 111)

    await run(broadcasts, reporter)

    [event] = await broadcast_events(session)
    assert event.admin_id == ADMIN_ID
    assert event.details == {
        "action": "broadcast",
        "preview_chat_id": ADMIN_CHAT_ID,
        "preview_message_id": PREVIEW.message_id,
        "recipients": 1,
        "text": TEXT,
    }


async def test_same_preview_is_sent_only_once(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111)
    await run(broadcasts, reporter)

    again = await broadcasts.start(ADMIN_ID, PREVIEW, TEXT, reporter)
    await broadcasts.wait()

    assert again.outcome is StartOutcome.ALREADY_HANDLED
    assert sender.recipients == [111]


async def test_simultaneous_presses_send_once(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111, 222)

    results = await asyncio.gather(
        broadcasts.start(ADMIN_ID, PREVIEW, TEXT, reporter),
        broadcasts.start(ADMIN_ID + 1, PREVIEW, TEXT, reporter),
    )
    await broadcasts.wait()

    assert sorted(r.outcome for r in results) == sorted(
        [StartOutcome.STARTED, StartOutcome.ALREADY_HANDLED]
    )
    assert sender.recipients == [111, 222]


async def test_second_broadcast_waits_for_the_first(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111)
    sender.gate = asyncio.Event()
    await broadcasts.start(ADMIN_ID, PREVIEW, TEXT, reporter)

    other = PreviewRef(chat_id=ADMIN_CHAT_ID, message_id=43)
    busy = await broadcasts.start(ADMIN_ID, other, TEXT, reporter)
    sender.gate.set()
    await broadcasts.wait()

    assert busy.outcome is StartOutcome.BUSY
    assert sender.recipients == [111]
    # занятая рассылка не тратит предпросмотр: его можно отправить потом
    assert (await broadcasts.start(ADMIN_ID, other, TEXT, reporter)).outcome is (
        StartOutcome.STARTED
    )
    await broadcasts.wait()


async def test_cancelled_preview_cannot_be_sent(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111)

    assert await broadcasts.cancel(ADMIN_ID, PREVIEW)
    started = await broadcasts.start(ADMIN_ID, PREVIEW, TEXT, reporter)

    assert started.outcome is StartOutcome.ALREADY_HANDLED
    assert sender.sent == []
    assert not await broadcasts.cancel(ADMIN_ID, PREVIEW)  # второй раз отменять нечего
    [event] = await broadcast_events(session)
    assert event.details == {
        "action": "broadcast_cancelled",
        "preview_chat_id": ADMIN_CHAT_ID,
        "preview_message_id": PREVIEW.message_id,
    }


async def test_sent_preview_cannot_be_cancelled(
    broadcasts: BroadcastService, session: AsyncSession, reporter: FakeReporter
) -> None:
    await add_clients(session, 111)
    await run(broadcasts, reporter)

    assert not await broadcasts.cancel(ADMIN_ID, PREVIEW)


# --- Темп и ошибки Telegram ---


async def test_pause_between_messages(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sleep: FakeSleep,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111, 222, 333)

    await run(broadcasts, reporter)

    assert sleep.waits == [BROADCAST_SEND_INTERVAL.total_seconds()] * 3


async def test_retry_after_waits_and_sends_again(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    sleep: FakeSleep,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111)
    sender.throttle[111] = [5]

    report = await run(broadcasts, reporter)

    assert sender.recipients == [111]
    assert 5 in sleep.waits
    assert report.progress.delivered == 1


async def test_endless_retry_after_gives_up_on_that_client(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111, 222)
    sender.throttle[111] = [1] * 100

    report = await run(broadcasts, reporter)

    assert sender.recipients == [222]
    assert report.progress == BroadcastProgress(total=2, delivered=1, blocked=0, failed=1)


async def test_blocked_and_failed_are_counted_and_sending_goes_on(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111, 222, 333)
    sender.outcomes = {111: DeliveryOutcome.BLOCKED, 222: DeliveryOutcome.FAILED}

    report = await run(broadcasts, reporter)

    assert sender.recipients == [111, 222, 333]
    assert report.progress == BroadcastProgress(total=3, delivered=1, blocked=1, failed=1)


async def test_many_failures_in_a_row_stop_the_broadcast(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    ids = list(range(1000, 1000 + MAX_CONSECUTIVE_FAILURES + 5))
    await add_clients(session, *ids)
    sender.outcomes = dict.fromkeys(ids, DeliveryOutcome.FAILED)

    report = await run(broadcasts, reporter)

    assert report.end is BroadcastEnd.ABORTED
    assert len(sender.sent) == MAX_CONSECUTIVE_FAILURES


async def test_progress_is_reported_along_the_way(
    broadcasts: BroadcastService, session: AsyncSession, reporter: FakeReporter
) -> None:
    await add_clients(session, *range(1000, 1000 + PROGRESS_EVERY + 1))

    await run(broadcasts, reporter)

    [progress] = reporter.progress_reports
    assert progress.delivered == PROGRESS_EVERY


# --- Остановка ---


async def test_admin_stops_running_broadcast(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111, 222)
    sender.gate = asyncio.Event()
    await broadcasts.start(ADMIN_ID, PREVIEW, TEXT, reporter)
    await asyncio.sleep(0)

    assert await broadcasts.stop(ADMIN_ID)

    assert sender.sent == []
    [report] = reporter.reports
    assert report.end is BroadcastEnd.STOPPED
    assert report.progress.total == 2
    actions = [
        event.details and event.details["action"] for event in await broadcast_events(session)
    ]
    assert actions == ["broadcast", "broadcast_stopped"]
    assert not await broadcasts.stop(ADMIN_ID)  # уже не идёт


async def test_shutdown_stops_broadcast_with_report(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111)
    sender.gate = asyncio.Event()
    await broadcasts.start(ADMIN_ID, PREVIEW, TEXT, reporter)
    await asyncio.sleep(0)

    await broadcasts.shutdown()

    [report] = reporter.reports
    assert report.end is BroadcastEnd.STOPPED
    assert len(await broadcast_events(session)) == 1  # остановка ботом — не действие админа


async def test_unexpected_error_is_reported(
    broadcasts: BroadcastService,
    session: AsyncSession,
    sender: FakeSender,
    reporter: FakeReporter,
) -> None:
    await add_clients(session, 111)

    async def broken(telegram_id: int, text: str) -> DeliveryOutcome:
        raise RuntimeError("база недоступна")

    sender.send = broken  # type: ignore[method-assign]

    report = await run(broadcasts, reporter)

    assert report.end is BroadcastEnd.CRASHED
    assert report.error == "RuntimeError: база недоступна"
