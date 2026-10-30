"""Льготный период по окончании платной подписки (задача 11): вебхук и сверка, сообщения, конец."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest
from conftest import NOW
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.models import Client, Event, Notification
from tsproxy.domain.compensation import CompensationAction
from tsproxy.domain.constants import BYTES_PER_GB, GRACE_DAYS, LAST_CHANCE_DAYS
from tsproxy.domain.grace import GraceStage
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus, RemnawaveUnavailable
from tsproxy.services.account_checks import AccountCheck, AccountChecks, CheckSource
from tsproxy.services.compensation_log import ACTION, AMOUNT_SECONDS
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.grace import GraceHandler
from tsproxy.services.masking import Masker

TELEGRAM_ID = 111  # клиент из conftest
PAID_UNTIL = NOW + timedelta(days=30)
GRACE = timedelta(days=GRACE_DAYS + LAST_CHANCE_DAYS)


@dataclass
class RecordingMessenger:
    """Отправка сообщений-подделка: запоминает, по желанию «клиент заблокировал» или падает."""

    sent: list[tuple[int, GraceStage, datetime]] = field(default_factory=list)
    blocked: bool = False
    error: Exception | None = None

    async def send_grace_notice(
        self, telegram_id: int, stage: GraceStage, access_until: datetime
    ) -> bool:
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        if self.blocked:
            return False
        self.sent.append((telegram_id, stage, access_until))
        return True

    @property
    def stages(self) -> list[GraceStage]:
        return [stage for _, stage, _ in self.sent]


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def messenger() -> RecordingMessenger:
    return RecordingMessenger()


@pytest.fixture
def handler(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    mask: Masker,
) -> GraceHandler:
    return GraceHandler(session_factory, remnawave, EventLog(clock, mask), messenger, clock)


@pytest.fixture
def checks(remnawave: FakeRemnawave, handler: GraceHandler, clock: FakeClock) -> AccountChecks:
    return AccountChecks(remnawave, RunMode.PROD, clock, [handler])


async def paid_account(remnawave: FakeRemnawave) -> None:
    """Оплаченный срок до `PAID_UNTIL`: трафик без лимита."""
    await remnawave.get_or_create_account(
        TELEGRAM_ID, expire_at=PAID_UNTIL, device_limit=3, traffic_limit_bytes=0
    )


def expire_in_panel(remnawave: FakeRemnawave, clock: FakeClock, at: datetime) -> None:
    """Часы дошли до `at`; срок кончился — панель ставит EXPIRED, как настоящая."""
    clock.now = at
    remnawave.set_status(TELEGRAM_ID, AccountStatus.EXPIRED)


async def reload(session: AsyncSession, client: Client) -> Client:
    await session.refresh(client)
    return client


async def events_of(session: AsyncSession, kind: EventKind) -> list[Event]:
    return list((await session.scalars(select(Event).where(Event.kind == kind.value))).all())


async def check_now(handler: GraceHandler, remnawave: FakeRemnawave, clock: FakeClock) -> None:
    """Проверка прямо обработчиком, без перечитывания (для сбоев панели на записи)."""
    account = await remnawave.get_account(TELEGRAM_ID)
    await handler.handle(
        AccountCheck(TELEGRAM_ID, account, CheckSource.RECONCILIATION, None, clock.now)
    )


# --- Начало льготного периода ---


async def test_paid_expiry_starts_grace(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)

    await checks.on_webhook("user.expired", f"tg_{TELEGRAM_ID}")

    assert (await reload(session, client)).grace_started_at == PAID_UNTIL
    account = await remnawave.get_account(TELEGRAM_ID)
    assert account.expire_at == PAID_UNTIL + GRACE
    assert account.status is AccountStatus.ACTIVE  # доступ снова есть
    assert messenger.sent == [(TELEGRAM_ID, GraceStage.STARTED, PAID_UNTIL + GRACE)]
    [event] = await events_of(session, EventKind.GRACE_STARTED)
    assert event.client_id == client.id


async def test_double_webhook_does_not_give_six_days(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)

    await checks.on_webhook("user.expired", f"tg_{TELEGRAM_ID}")
    clock.advance(timedelta(seconds=10))
    await checks.on_webhook("user.expired", f"tg_{TELEGRAM_ID}")
    await checks.check_all()

    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == PAID_UNTIL + GRACE
    assert remnawave.writes.count(("set_expiry", f"tg_{TELEGRAM_ID}")) == 1
    assert messenger.stages == [GraceStage.STARTED]
    assert len(await events_of(session, EventKind.GRACE_STARTED)) == 1


async def test_bot_was_down_reconciliation_gives_full_grace(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    # Вебхук потерялся: бот лежал 5 часов после окончания срока
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL + timedelta(hours=5))

    await checks.check_all()

    now = clock.now
    assert (await reload(session, client)).grace_started_at == now
    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == now + GRACE
    assert messenger.stages == [GraceStage.STARTED]


async def test_trial_end_gets_no_grace(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    client.trial_granted_at = NOW
    await session.commit()
    await remnawave.get_or_create_account(
        TELEGRAM_ID,
        expire_at=NOW + timedelta(days=7),
        device_limit=3,
        traffic_limit_bytes=7 * BYTES_PER_GB,
    )
    expire_in_panel(remnawave, clock, NOW + timedelta(days=7))

    await checks.on_webhook("user.expired", f"tg_{TELEGRAM_ID}")

    assert (await reload(session, client)).grace_started_at is None
    assert remnawave.writes == [("create", f"tg_{TELEGRAM_ID}")]
    assert messenger.sent == []


async def test_paid_after_trial_gets_grace(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    # Пробный был, потом оплата сняла лимит трафика — это уже платный срок
    client.trial_granted_at = NOW - timedelta(days=20)
    await session.commit()
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)

    await checks.check_all()

    assert (await reload(session, client)).grace_started_at == PAID_UNTIL


async def test_active_subscription_is_left_alone(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    session: AsyncSession,
    client: Client,
) -> None:
    await paid_account(remnawave)

    await checks.check_all()

    assert (await reload(session, client)).grace_started_at is None
    assert remnawave.writes == [("create", f"tg_{TELEGRAM_ID}")]
    assert messenger.sent == []


async def test_account_without_client_row_is_skipped(
    checks: AccountChecks, remnawave: FakeRemnawave, messenger: RecordingMessenger, clock: FakeClock
) -> None:
    # Клиента 111 в базе нет: отметку ставить некуда — ничего не трогаем
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)

    report = await checks.check_all()

    assert report.failures == 0
    assert remnawave.writes == [("create", f"tg_{TELEGRAM_ID}")]
    assert messenger.sent == []


async def test_panel_down_after_mark_is_fixed_by_next_check(
    handler: GraceHandler,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    account = await remnawave.get_account(TELEGRAM_ID)
    remnawave.fail_next(RemnawaveUnavailable("таймаут"))

    with pytest.raises(RemnawaveUnavailable):
        await handler.handle(
            AccountCheck(TELEGRAM_ID, account, CheckSource.WEBHOOK, "user.expired", clock.now)
        )
    assert (await reload(session, client)).grace_started_at == PAID_UNTIL
    assert messenger.sent == []  # о сохранённом доступе не пишем, пока его нет в панели

    clock.advance(timedelta(minutes=5))
    await check_now(handler, remnawave, clock)

    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == PAID_UNTIL + GRACE
    assert messenger.stages == [GraceStage.STARTED]


# --- Напоминания каждые сутки и «последний день» ---


async def test_one_reminder_per_day_then_last_day_warning(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    client: Client,
) -> None:
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    await checks.check_all()

    # сверка каждые 5 минут почти четыре дня — по одному сообщению в сутки
    while clock.now < PAID_UNTIL + GRACE - timedelta(minutes=5):
        clock.advance(timedelta(minutes=5))
        await checks.check_all()

    assert messenger.stages == [
        GraceStage.STARTED,
        GraceStage.REMINDER,
        GraceStage.REMINDER,
        GraceStage.LAST_DAY,
    ]
    assert {until for _, _, until in messenger.sent} == {PAID_UNTIL + GRACE}


async def test_bot_down_for_days_sends_only_todays_notice(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    client: Client,
) -> None:
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    await checks.check_all()

    clock.advance(timedelta(days=GRACE_DAYS, hours=2))  # бот лежал, пропустил напоминания
    await checks.check_all()

    assert messenger.stages == [GraceStage.STARTED, GraceStage.LAST_DAY]


async def test_payment_during_grace_stops_reminders(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    client: Client,
) -> None:
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    await checks.check_all()
    # Оплата (задача 13) продлила срок; отметку снимет она же — но и до этого не напоминаем
    renewed_until = clock.now + timedelta(days=30)
    await remnawave.set_expiry(TELEGRAM_ID, renewed_until)

    clock.advance(timedelta(days=1, minutes=1))
    await checks.check_all()

    assert messenger.stages == [GraceStage.STARTED]
    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == renewed_until


async def test_blocked_client_gets_no_messages_but_keeps_grace(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    client.bot_blocked_at = NOW
    await session.commit()
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)

    await checks.check_all()

    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == PAID_UNTIL + GRACE
    assert messenger.sent == []
    assert (await session.scalars(select(Notification))).all() == []


async def test_telegram_failure_is_retried_by_next_check(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    client: Client,
) -> None:
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    messenger.error = RuntimeError("Telegram не ответил")

    report = await checks.check_all()
    assert report.failures == 1  # не прячем: сверка поднимет тревогу

    clock.advance(timedelta(minutes=5))
    await checks.check_all()

    assert messenger.stages == [GraceStage.STARTED]


async def test_client_blocked_bot_during_send_is_not_retried(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    client: Client,
) -> None:
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    messenger.blocked = True
    await checks.check_all()

    messenger.blocked = False
    clock.advance(timedelta(minutes=5))
    await checks.check_all()

    assert messenger.sent == []


# --- Конец льготного периода ---


async def grace_ended(checks: AccountChecks, remnawave: FakeRemnawave, clock: FakeClock) -> None:
    """Льготный начался и прошёл: панель снова поставила EXPIRED."""
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    await checks.check_all()
    expire_in_panel(remnawave, clock, PAID_UNTIL + GRACE)


async def test_after_grace_access_is_disabled_not_deleted(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await grace_ended(checks, remnawave, clock)

    await checks.on_webhook("user.expired", f"tg_{TELEGRAM_ID}")

    account = await remnawave.get_account(TELEGRAM_ID)  # аккаунт на месте
    assert account.status is AccountStatus.DISABLED
    assert ("delete", f"tg_{TELEGRAM_ID}") not in remnawave.writes
    assert messenger.stages == [GraceStage.STARTED, GraceStage.ENDED]
    [event] = await events_of(session, EventKind.GRACE_ENDED)
    assert event.client_id == client.id


async def test_final_message_only_once(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await grace_ended(checks, remnawave, clock)

    for _ in range(3):
        await checks.check_all()
        clock.advance(timedelta(days=1))

    assert messenger.stages == [GraceStage.STARTED, GraceStage.ENDED]
    assert len(await events_of(session, EventKind.GRACE_ENDED)) == 1


async def test_final_message_retried_after_telegram_failure(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    client: Client,
) -> None:
    await grace_ended(checks, remnawave, clock)
    messenger.error = RuntimeError("Telegram не ответил")
    await checks.check_all()  # доступ выключен, сообщение не ушло

    clock.advance(timedelta(minutes=5))
    await checks.check_all()

    assert messenger.stages == [GraceStage.STARTED, GraceStage.ENDED]


async def test_panel_down_at_end_is_retried(
    handler: GraceHandler,
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
    client: Client,
) -> None:
    await grace_ended(checks, remnawave, clock)
    account = await remnawave.get_account(TELEGRAM_ID)
    remnawave.fail_next(RemnawaveUnavailable("таймаут"))
    with pytest.raises(RemnawaveUnavailable):
        await handler.handle(
            AccountCheck(TELEGRAM_ID, account, CheckSource.RECONCILIATION, None, clock.now)
        )

    clock.advance(timedelta(minutes=5))
    await checks.check_all()

    assert (await remnawave.get_account(TELEGRAM_ID)).status is AccountStatus.DISABLED
    assert messenger.stages == [GraceStage.STARTED, GraceStage.ENDED]
    assert len(await events_of(session, EventKind.GRACE_ENDED)) == 1


async def test_disabled_by_admin_during_grace_gets_no_final_message(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    client: Client,
) -> None:
    # Администратор выключил доступ сам — это не конец льготного, финальное сообщение не наше
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    await checks.check_all()
    await remnawave.disable_account(TELEGRAM_ID)

    clock.advance(GRACE + timedelta(days=1))
    await checks.check_all()

    assert messenger.stages == [GraceStage.STARTED]


async def test_extended_by_admin_after_grace_is_not_disabled(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    client: Client,
) -> None:
    await grace_ended(checks, remnawave, clock)
    await remnawave.set_expiry(TELEGRAM_ID, clock.now + timedelta(days=7))

    await checks.check_all()

    assert (await remnawave.get_account(TELEGRAM_ID)).status is AccountStatus.ACTIVE
    assert messenger.stages == [GraceStage.STARTED]


# --- Льготный, продлённый компенсацией за сбой (задача 20) ---

COMPENSATION = timedelta(days=1, hours=3)
EXTENDED_END = PAID_UNTIL + GRACE + COMPENSATION


async def record_compensation(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
    action: CompensationAction = CompensationAction.EXTEND_GRACE,
) -> None:
    """Начисление компенсации в журнале — как его пишет `CompensationService`."""
    async with session_factory() as session:
        await EventLog(clock, mask).record(
            session,
            EventKind.COMPENSATION_GRANTED,
            client_id=client.id,
            details={ACTION: action, AMOUNT_SECONDS: int(COMPENSATION.total_seconds())},
        )
        await session.commit()


@pytest.fixture
async def compensated_in_grace(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
    mask: Masker,
    client: Client,
) -> None:
    """Льготный идёт сутки; компенсация продлила его — запись в журнале и срок в панели."""
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    await checks.check_all()
    clock.advance(timedelta(days=1))
    await record_compensation(session_factory, clock, mask, client)
    await remnawave.set_expiry(TELEGRAM_ID, EXTENDED_END)


@pytest.mark.usefixtures("compensated_in_grace")
async def test_compensated_grace_is_not_rolled_back_at_usual_end(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session: AsyncSession,
) -> None:
    clock.now = PAID_UNTIL + GRACE + timedelta(hours=1)  # обычный льготный уже кончился бы

    await checks.check_all()

    account = await remnawave.get_account(TELEGRAM_ID)
    assert account.expire_at == EXTENDED_END
    assert account.status is AccountStatus.ACTIVE
    assert GraceStage.ENDED not in messenger.stages
    assert await events_of(session, EventKind.GRACE_ENDED) == []


@pytest.mark.usefixtures("compensated_in_grace")
async def test_compensated_grace_messages_use_new_end_and_shifted_last_day(
    checks: AccountChecks, messenger: RecordingMessenger, clock: FakeClock
) -> None:
    while clock.now < EXTENDED_END - timedelta(minutes=5):
        clock.advance(timedelta(minutes=5))
        await checks.check_all()

    assert messenger.stages.count(GraceStage.LAST_DAY) == 1
    assert messenger.stages[-1] is GraceStage.LAST_DAY
    assert messenger.sent[-1][2] == EXTENDED_END
    assert GraceStage.ENDED not in messenger.stages


@pytest.mark.usefixtures("compensated_in_grace")
async def test_compensated_grace_ends_at_new_end(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
) -> None:
    expire_in_panel(remnawave, clock, EXTENDED_END)

    await checks.check_all()

    assert (await remnawave.get_account(TELEGRAM_ID)).status is AccountStatus.DISABLED
    assert messenger.sent[-1] == (TELEGRAM_ID, GraceStage.ENDED, EXTENDED_END)


async def test_recorded_compensation_missing_in_panel_is_set_by_grace_check(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
    mask: Masker,
    client: Client,
) -> None:
    # компенсация записана, а панель срок не приняла — обработчик льготного доводит срок сам
    await paid_account(remnawave)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    await checks.check_all()
    clock.advance(timedelta(hours=2))
    await record_compensation(session_factory, clock, mask, client)

    await checks.check_all()

    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == EXTENDED_END


async def test_compensation_of_paid_term_does_not_extend_grace(
    checks: AccountChecks,
    remnawave: FakeRemnawave,
    messenger: RecordingMessenger,
    clock: FakeClock,
    session_factory: async_sessionmaker[AsyncSession],
    mask: Masker,
    client: Client,
) -> None:
    # продление оплаченного срока (до льготного) — уже в сроке, льготный от него не длиннее
    await paid_account(remnawave)
    await record_compensation(session_factory, clock, mask, client, CompensationAction.EXTEND)
    expire_in_panel(remnawave, clock, PAID_UNTIL)
    await checks.check_all()

    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == PAID_UNTIL + GRACE
    assert messenger.sent == [(TELEGRAM_ID, GraceStage.STARTED, PAID_UNTIL + GRACE)]
