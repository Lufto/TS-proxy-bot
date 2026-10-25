"""Напоминания и продления пробного: решают по текущему состоянию, каждое — один раз,
сколько бы раз ни пришли вебхук и сверка (задача 10)."""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from conftest import NOW
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import NotificationKind
from tsproxy.db.models import Client, Event, Notification
from tsproxy.domain.reminders import AccessPeriod, Reminder, ReminderKind, TrialTrafficNotice
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus, RemnawaveUnavailable
from tsproxy.services.account_checks import AccountCheck, CheckSource
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.reminders import (
    ClientNotice,
    ExpiryReminder,
    ReminderService,
    TrialExtended,
    TrialFarewell,
    TrialTraffic,
)

TELEGRAM_ID = 111
GB = 1024**3
TRIAL_LIMIT = 7 * GB
TRIAL_END = NOW + timedelta(days=7)
EXTENSION = timedelta(days=3)


@dataclass
class RecordingNotifier:
    """Подделка отправки клиенту: запоминает уведомления, по желанию падает."""

    sent: list[tuple[int, ClientNotice]] = field(default_factory=list)
    error: Exception | None = None

    async def notify(self, telegram_id: int, notice: ClientNotice) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((telegram_id, notice))

    @property
    def notices(self) -> list[ClientNotice]:
        return [notice for _, notice in self.sent]


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    mask: Masker,
) -> ReminderService:
    return ReminderService(session_factory, remnawave, EventLog(clock, mask), notifier, clock)


async def start_trial(remnawave: FakeRemnawave, session: AsyncSession, client: Client) -> None:
    """Пробный, выданный в `NOW`: 7 дней, 7 ГБ."""
    await remnawave.get_or_create_account(
        TELEGRAM_ID, expire_at=TRIAL_END, device_limit=3, traffic_limit_bytes=TRIAL_LIMIT
    )
    client.trial_granted_at = NOW
    await session.commit()


async def start_paid(remnawave: FakeRemnawave) -> None:
    """Оплаченный срок до `TRIAL_END`: трафик без лимита, пробного не было."""
    await remnawave.get_or_create_account(
        TELEGRAM_ID, expire_at=TRIAL_END, device_limit=3, traffic_limit_bytes=0
    )


async def check(service: ReminderService, remnawave: FakeRemnawave, clock: FakeClock) -> None:
    """Одна проверка аккаунта — как по сверке: свежее состояние из панели."""
    account = await remnawave.get_account(TELEGRAM_ID)
    await service.handle(
        AccountCheck(TELEGRAM_ID, account, CheckSource.RECONCILIATION, None, clock.now)
    )


async def extensions(session: AsyncSession, client: Client) -> int:
    await session.refresh(client)
    return client.trial_extensions


async def count_rows(session: AsyncSession, model: type[Notification] | type[Event]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


# --- Напоминания за день и за час ---


async def test_paid_reminders_day_and_hour_before_each_once(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    client: Client,
) -> None:
    await start_paid(remnawave)
    await check(service, remnawave, clock)
    assert notifier.sent == []

    clock.now = TRIAL_END - timedelta(hours=23)
    await check(service, remnawave, clock)
    await check(service, remnawave, clock)
    clock.advance(timedelta(minutes=5))
    await check(service, remnawave, clock)

    clock.now = TRIAL_END - timedelta(minutes=50)
    await check(service, remnawave, clock)
    await check(service, remnawave, clock)

    assert notifier.sent == [
        (
            TELEGRAM_ID,
            ExpiryReminder(Reminder(ReminderKind.DAY_BEFORE, AccessPeriod.PAID, TRIAL_END)),
        ),
        (
            TELEGRAM_ID,
            ExpiryReminder(Reminder(ReminderKind.HOUR_BEFORE, AccessPeriod.PAID, TRIAL_END)),
        ),
    ]


async def test_renewed_subscription_gets_new_reminders(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    client: Client,
) -> None:
    await start_paid(remnawave)
    clock.now = TRIAL_END - timedelta(hours=5)
    await check(service, remnawave, clock)
    renewed = TRIAL_END + timedelta(days=30)
    await remnawave.set_expiry(TELEGRAM_ID, renewed)

    clock.now = renewed - timedelta(hours=5)
    await check(service, remnawave, clock)

    assert [n.reminder.ends_at for n in notifier.notices if isinstance(n, ExpiryReminder)] == [
        TRIAL_END,
        renewed,
    ]


async def test_connected_trial_gets_trial_reminder(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    remnawave.simulate_connection(TELEGRAM_ID, NOW + timedelta(hours=1))
    clock.now = TRIAL_END - timedelta(hours=5)

    await check(service, remnawave, clock)

    assert notifier.notices == [
        ExpiryReminder(Reminder(ReminderKind.DAY_BEFORE, AccessPeriod.TRIAL, TRIAL_END))
    ]


async def test_never_connected_trial_gets_no_reminder(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    clock.now = TRIAL_END - timedelta(minutes=30)

    await check(service, remnawave, clock)

    assert notifier.sent == []


async def test_grace_gets_no_reminder(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_paid(remnawave)
    grace_end = TRIAL_END + timedelta(days=4)
    clock.now = TRIAL_END
    await remnawave.set_expiry(TELEGRAM_ID, grace_end)  # так льготный ставит задача 11
    client.grace_started_at = TRIAL_END
    await session.commit()

    # в льготный период напоминает задача 11 — иначе предупреждения пришли бы дважды
    for left in (timedelta(hours=20), timedelta(minutes=30)):
        clock.now = grace_end - left
        await check(service, remnawave, clock)

    assert notifier.sent == []


async def test_disabled_account_gets_nothing(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    await remnawave.disable_account(TELEGRAM_ID)
    clock.now = TRIAL_END + timedelta(minutes=1)
    remnawave.writes.clear()

    await check(service, remnawave, clock)

    assert notifier.sent == []
    assert remnawave.writes == []
    assert await extensions(session, client) == 0


async def test_blocked_client_gets_no_reminder_and_no_mark(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_paid(remnawave)
    client.bot_blocked_at = NOW
    await session.commit()
    clock.now = TRIAL_END - timedelta(hours=5)

    await check(service, remnawave, clock)

    assert notifier.sent == []
    # отметки нет: вернётся через /start, пока срок не прошёл — напоминание придёт
    assert await count_rows(session, Notification) == 0


async def test_account_without_client_in_database_is_skipped(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
) -> None:
    await start_paid(remnawave)
    clock.now = TRIAL_END - timedelta(hours=5)

    await check(service, remnawave, clock)

    assert notifier.sent == []


async def test_failed_send_is_not_repeated(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    client: Client,
) -> None:
    # Отметка ставится до отправки: лучше потерять одно напоминание, чем прислать дважды
    await start_paid(remnawave)
    clock.now = TRIAL_END - timedelta(hours=5)
    notifier.error = RuntimeError("Telegram упал")

    with pytest.raises(RuntimeError):
        await check(service, remnawave, clock)
    notifier.error = None
    await check(service, remnawave, clock)

    assert notifier.sent == []


# --- Продления пробного и прощание ---


async def test_never_connected_trial_is_extended_once(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    clock.now = TRIAL_END + timedelta(minutes=2)

    await check(service, remnawave, clock)
    await check(service, remnawave, clock)

    new_end = clock.now + EXTENSION
    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == new_end
    assert notifier.notices == [TrialExtended(new_end)]
    assert await extensions(session, client) == 1
    [event] = await session.scalars(
        select(Event).where(Event.kind == EventKind.TRIAL_EXTENDED.value)
    )
    assert event.client_id == client.id


async def test_five_extensions_then_one_farewell(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    for _ in range(7):
        clock.now = (await remnawave.get_account(TELEGRAM_ID)).expire_at
        await check(service, remnawave, clock)
    clock.advance(timedelta(days=1))
    await check(service, remnawave, clock)

    extended = [n for n in notifier.notices if isinstance(n, TrialExtended)]
    assert len(extended) == 5
    assert notifier.notices[-1] == TrialFarewell()
    assert notifier.notices.count(TrialFarewell()) == 1
    assert await extensions(session, client) == 5
    # 7 + 5 × 3 = 22 дня
    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == NOW + timedelta(days=22)


async def test_connected_trial_is_not_extended(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    remnawave.simulate_connection(TELEGRAM_ID, NOW + timedelta(hours=1))
    remnawave.writes.clear()
    clock.now = TRIAL_END + timedelta(minutes=1)

    await check(service, remnawave, clock)

    assert remnawave.writes == []
    assert notifier.sent == []
    assert await extensions(session, client) == 0


async def test_paid_expiry_is_left_to_grace(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    client: Client,
) -> None:
    # окончание оплаченного срока — льготный период, задача 11
    await start_paid(remnawave)
    remnawave.writes.clear()
    clock.now = TRIAL_END + timedelta(minutes=1)

    await check(service, remnawave, clock)

    assert remnawave.writes == []
    assert notifier.sent == []


async def test_extension_survives_panel_failure(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    clock.now = TRIAL_END + timedelta(minutes=1)
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    with pytest.raises(RemnawaveUnavailable):
        await check(service, remnawave, clock)
    assert notifier.sent == []

    clock.advance(timedelta(minutes=5))  # следующая сверка
    await check(service, remnawave, clock)
    await check(service, remnawave, clock)

    new_end = clock.now + EXTENSION
    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == new_end
    assert notifier.notices == [TrialExtended(new_end)]
    assert await extensions(session, client) == 1  # продление засчитано один раз


async def test_simultaneous_checks_extend_once(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    clock.now = TRIAL_END + timedelta(minutes=1)
    account = await remnawave.get_account(TELEGRAM_ID)
    probe = AccountCheck(TELEGRAM_ID, account, CheckSource.WEBHOOK, "user.expired", clock.now)

    await asyncio.gather(service.handle(probe), service.handle(probe))

    assert await extensions(session, client) == 1
    assert len(notifier.notices) == 1


async def test_blocked_client_trial_is_extended_silently(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    client.bot_blocked_at = NOW
    await session.commit()
    clock.now = TRIAL_END

    await check(service, remnawave, clock)

    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == TRIAL_END + EXTENSION
    assert notifier.sent == []


# --- Трафик пробного ---


async def test_trial_traffic_warning_then_used_up_each_once(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    remnawave.simulate_connection(TELEGRAM_ID, NOW)
    remnawave.set_traffic_used(TELEGRAM_ID, 6 * GB)
    await check(service, remnawave, clock)
    await check(service, remnawave, clock)

    remnawave.set_traffic_used(TELEGRAM_ID, TRIAL_LIMIT)
    remnawave.set_status(TELEGRAM_ID, AccountStatus.LIMITED)
    await check(service, remnawave, clock)
    await check(service, remnawave, clock)

    assert notifier.notices == [
        TrialTraffic(TrialTrafficNotice.WARNING, 6 * GB, TRIAL_LIMIT),
        TrialTraffic(TrialTrafficNotice.USED_UP, TRIAL_LIMIT, TRIAL_LIMIT),
    ]


async def test_used_up_without_warning_sends_only_used_up(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await start_trial(remnawave, session, client)
    remnawave.set_traffic_used(TELEGRAM_ID, TRIAL_LIMIT)
    remnawave.set_status(TELEGRAM_ID, AccountStatus.LIMITED)

    await check(service, remnawave, clock)

    assert notifier.notices == [TrialTraffic(TrialTrafficNotice.USED_UP, TRIAL_LIMIT, TRIAL_LIMIT)]
    rows = list(await session.scalars(select(Notification.kind)))
    assert rows == [NotificationKind.TRIAL_TRAFFIC_USED_UP]


async def test_paid_account_gets_no_traffic_notice(
    service: ReminderService,
    remnawave: FakeRemnawave,
    notifier: RecordingNotifier,
    clock: FakeClock,
    client: Client,
) -> None:
    await start_paid(remnawave)
    remnawave.set_traffic_used(TELEGRAM_ID, 500 * GB)

    await check(service, remnawave, clock)

    assert notifier.sent == []
