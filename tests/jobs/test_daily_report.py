"""Ежедневный отчёт собирается на тестовых данных: продажи за вчера, хвосты, бэкапы, задачи."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from backup_helpers import CHAT_ID, REPO_CONTENT
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts, load_content
from tsproxy.db.engine import create_engine, create_session_factory
from tsproxy.db.enums import OrderStatus, TicketStatus
from tsproxy.db.models import Base, Client, Event, Order, SupportTicket
from tsproxy.jobs.backup import BackupResult
from tsproxy.jobs.backup_files import StoredBackup
from tsproxy.jobs.daily_report import BotBackupJob, DailyReport, ReportDestination
from tsproxy.logging_setup import MaskingFilter
from tsproxy.services.alerts import Alerter
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.job_health import JobHealth
from tsproxy.services.panel_backups import PanelBackupMonitor

# Отчёт уходит в 10:00 МСК 25.10 и рассказывает про 24.10 (по Москве)
REPORT_AT = datetime(2026, 10, 25, 7, 0, tzinfo=UTC)
DAY_START = datetime(2026, 10, 23, 21, 0, tzinfo=UTC)  # 00:00 МСК 24.10
IN_DAY = DAY_START + timedelta(hours=10)
BEFORE_DAY = DAY_START - timedelta(minutes=30)
AFTER_DAY = DAY_START + timedelta(days=1, minutes=30)
REPORT_THREAD_ID = 7


@dataclass
class StubSupport:
    overdue: list[object] = field(default_factory=list)

    async def overdue_tickets(self) -> list[object]:
        return self.overdue


@dataclass
class StubBackup:
    result: BackupResult

    async def run(self) -> BackupResult:
        return self.result


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'report.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def texts() -> Texts:
    return load_content(REPO_CONTENT).admin_texts


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(BEFORE_DAY - timedelta(days=1))  # бот запущен позавчера


@pytest.fixture
def alerter(clock: FakeClock, texts: Texts) -> Alerter:
    return Alerter(FakeAlertSender(), texts, CHAT_ID, 2, clock, MaskingFilter([]).mask)


def touch(path: Path, at: datetime, size: int = 3 * 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (at.timestamp(), at.timestamp()))
    return path


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        clients = [
            Client(telegram_id=1, first_seen_at=BEFORE_DAY),
            Client(telegram_id=2, first_seen_at=IN_DAY),
            Client(telegram_id=3, first_seen_at=IN_DAY + timedelta(hours=1)),
            Client(telegram_id=4, first_seen_at=AFTER_DAY),
        ]
        session.add_all(clients)
        await session.flush()
        first = clients[0].id

        def order(paid_at: datetime | None, amount: str, status: OrderStatus) -> Order:
            return Order(
                client_id=first,
                tariff_id="m1",
                price=Decimal(amount),
                days=30,
                status=status,
                paid_at=paid_at,
                paid_amount=Decimal(amount) if paid_at is not None else None,
            )

        session.add_all(
            [
                order(IN_DAY, "405.00", OrderStatus.ACCESS_GRANTED),
                order(IN_DAY, "150.50", OrderStatus.ACCESS_GRANTED),
                order(BEFORE_DAY, "150.00", OrderStatus.ACCESS_GRANTED),
                order(AFTER_DAY, "150.00", OrderStatus.PAID),  # доступ ещё не выдан
                order(None, "150.00", OrderStatus.CREATED),
                Event(kind=EventKind.TRIAL_GRANTED.value, occurred_at=IN_DAY, client_id=first),
                Event(kind=EventKind.TRIAL_GRANTED.value, occurred_at=IN_DAY, client_id=first),
                Event(kind=EventKind.TRIAL_GRANTED.value, occurred_at=AFTER_DAY, client_id=first),
                Event(kind=EventKind.ORDER_CREATED.value, occurred_at=IN_DAY, client_id=first),
                SupportTicket(client_id=first, status=TicketStatus.OPEN),
                SupportTicket(client_id=first, status=TicketStatus.OPEN),
                SupportTicket(client_id=first, status=TicketStatus.CLOSED),
            ]
        )
        await session.commit()


def make_report(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    alerter: Alerter,
    jobs: JobHealth,
    sender: FakeAlertSender,
    texts: Texts,
    clock: FakeClock,
    bot_backup_job: BotBackupJob | None,
) -> DailyReport:
    panel = PanelBackupMonitor(
        tmp_path / "panel" / "backup.log", tmp_path / "panel" / "daily", alerter, texts, clock
    )
    return DailyReport(
        session_factory,
        StubSupport([object()]),
        alerter,
        jobs,
        panel,
        tmp_path / "bot",
        bot_backup_job,
        sender,
        ReportDestination(CHAT_ID, REPORT_THREAD_ID),
        texts,
        clock,
    )


async def test_report_on_test_data(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    alerter: Alerter,
    texts: Texts,
    clock: FakeClock,
) -> None:
    await seed(session_factory)
    jobs = JobHealth(clock)
    jobs.watch("remnawave_reconcile", timedelta(minutes=15))
    jobs.watch("alerts_flush", timedelta(minutes=5))
    # тревоги: две вчера, одна позавчера
    clock.now = BEFORE_DAY
    await alerter.raise_alert("a", "позавчера")
    clock.now = IN_DAY
    await alerter.raise_alert("b", "вчера")
    await alerter.raise_alert("b", "вчера повтор")
    # бэкап панели в 04:00 МСК и бэкап бота в 04:30 МСК сегодня
    panel_copy = touch(
        tmp_path / "panel" / "daily" / "remnawave-2026-10-25.tar.gz.age",
        datetime(2026, 10, 25, 1, 0, tzinfo=UTC),
        size=208 * 1024,
    )
    (tmp_path / "panel" / "backup.log").write_text(
        f"2026-10-25T01:00:03+00:00 OK: {panel_copy} (208K)\n", encoding="utf-8"
    )
    bot_copy = touch(
        tmp_path / "bot" / "daily" / "tsproxy-2026-10-25.db.gz.age",
        datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
    )
    bot_job = BotBackupJob(
        StubBackup(
            BackupResult(
                StoredBackup(daily=bot_copy, monthly=bot_copy, size_bytes=3 * 1024),
                removed=(),
                sent_to_telegram=True,
            )
        )
    )
    await bot_job.run()
    clock.now = REPORT_AT
    jobs.succeeded("alerts_flush")  # а сверка не отработала ни разу — зависла
    sender = FakeAlertSender()

    report = make_report(tmp_path, session_factory, alerter, jobs, sender, texts, clock, bot_job)
    await report.send()

    [message] = sender.sent
    assert (message.chat_id, message.thread_id) == (CHAT_ID, REPORT_THREAD_ID)
    lines = message.text.splitlines()
    assert lines == [
        "📊 <b>Отчёт за 24.10.2026</b>",
        "👤 Новых клиентов: 2",
        "🎁 Пробных выдано: 2",
        "💰 Оплат: 2 на 555.50 ₽",
        "⚠️ Оплачено, но доступ не выдан: 1 — проверьте «💰 Оплаты» и «🚨 Тревоги»",
        "💬 Открытых обращений: 2, ждут ответа дольше часа: 1",
        "🚨 Тревог: 2",
        "💾 База бота: копия 25.10.2026, 04:30 МСК, 3.0 КБ, в Telegram отправлена",
        "💾 Панель: копия 25.10.2026, 04:00 МСК, 208.0 КБ, ошибок отправки в Telegram нет",
        "⏱ Фоновые задачи: ⚠️ не отрабатывают — remnawave_reconcile",
    ]


async def test_quiet_day_with_problems(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    texts: Texts,
    clock: FakeClock,
) -> None:
    """Пустой день, бот перезапущен вчера днём, копий нет, в dev бэкап бота не запускается."""
    clock.now = IN_DAY
    alerter = Alerter(FakeAlertSender(), texts, CHAT_ID, 2, clock, MaskingFilter([]).mask)
    clock.now = REPORT_AT
    jobs = JobHealth(clock)
    sender = FakeAlertSender()

    report = make_report(tmp_path, session_factory, alerter, jobs, sender, texts, clock, None)
    text = await report.build()

    assert "💰 Оплат: 0 на 0.00 ₽" in text
    assert "Оплачено, но доступ не выдан" not in text
    assert "🚨 Тревог: 0 (считаем с запуска бота 24.10.2026, 10:00 МСК)" in text
    assert "💾 База бота: бэкап не запускается (режим dev)" in text
    assert "💾 Панель: ⚠️ ни одной копии" in text
    assert "⏱ Фоновые задачи: все работают" in text


async def test_bot_copy_made_before_restart_and_old_panel_copy(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    alerter: Alerter,
    texts: Texts,
    clock: FakeClock,
) -> None:
    clock.now = REPORT_AT
    touch(
        tmp_path / "bot" / "daily" / "tsproxy-2026-10-25.db.gz.age", REPORT_AT - timedelta(hours=5)
    )
    touch(
        tmp_path / "panel" / "daily" / "remnawave-2026-10-23.tar.gz.age",
        REPORT_AT - timedelta(hours=30),
    )
    bot_job = BotBackupJob(StubBackup(BackupResult(StoredBackup(Path(), Path(), 0), (), True)))

    report = make_report(
        tmp_path,
        session_factory,
        alerter,
        JobHealth(clock),
        FakeAlertSender(),
        texts,
        clock,
        bot_job,
    )
    text = await report.build()

    assert "💾 База бота: копия 25.10.2026, 05:00 МСК, 3.0 КБ\n" in text
    assert "💾 Панель: ⚠️ нет копии моложе 26 часов (последняя — 24.10.2026, 04:00 МСК)" in text
