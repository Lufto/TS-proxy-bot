"""Админка (задача 18): поиск клиента, карточка, ручная выдача с пределом, сброс пробного,
устройства и перевыпуск ссылки. Каждое действие — в журнале с Telegram ID администратора."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.domain.admin import ClientQuery, GrantDaysOutOfRange
from tsproxy.domain.constants import (
    BYTES_PER_GB,
    MAX_MANUAL_GRANT_DAYS,
    PAID_DEVICE_LIMIT,
    TRIAL_TRAFFIC_GB,
    UNLIMITED_TRAFFIC_BYTES,
)
from tsproxy.domain.profile import ProfileStatus
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountStatus, Device, RemnawaveUnavailable
from tsproxy.services.admin import (
    AdminActionKind,
    AdminService,
    ExtensionOutcome,
    RevokeOutcome,
    TrialResetOutcome,
)
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock, FakeTrialGate
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.profile import ProfileService, device_ref
from tsproxy.services.trial import TrialOutcome, TrialService

TELEGRAM_ID = 111  # клиент из фикстуры `client`
OTHER_TELEGRAM_ID = 222
ADMIN_ID = 900
TRIAL_TRAFFIC = TRIAL_TRAFFIC_GB * BYTES_PER_GB


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def events(clock: FakeClock, mask: Masker) -> EventLog:
    return EventLog(clock, mask)


@pytest.fixture
def term_locks() -> KeyedLocks:
    return KeyedLocks()


@pytest.fixture
def admin(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    events: EventLog,
    clock: FakeClock,
    term_locks: KeyedLocks,
) -> AdminService:
    return AdminService(
        session_factory, remnawave, ProfileService(remnawave, clock), events, clock, term_locks
    )


@pytest.fixture
def trials(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    events: EventLog,
    clock: FakeClock,
) -> TrialService:
    return TrialService(session_factory, remnawave, events, clock, FakeTrialGate())


async def admin_events(session: AsyncSession) -> list[Event]:
    query = select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value).order_by(Event.id)
    return list(await session.scalars(query))


async def paid_account(remnawave: FakeRemnawave, expire_at: datetime) -> None:
    await remnawave.get_or_create_account(
        TELEGRAM_ID,
        expire_at=expire_at,
        device_limit=PAID_DEVICE_LIMIT,
        traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
    )


def phone(hwid: str, at: datetime) -> Device:
    return Device(hwid=hwid, platform="iOS", os_version="18", device_model="iPhone", created_at=at)


# --- Поиск ---


async def test_find_by_telegram_id(admin: AdminService, client: Client) -> None:
    [found] = await admin.find_clients(ClientQuery(telegram_id=TELEGRAM_ID))
    assert found.id == client.id


async def test_find_by_username_ignores_case(
    admin: AdminService, session: AsyncSession, client: Client
) -> None:
    client.username = "Ivan_Petrov"
    session.add(client)
    await session.commit()

    [found] = await admin.find_clients(ClientQuery(username="ivan_petrov"))

    assert found.telegram_id == TELEGRAM_ID


async def test_old_username_gives_every_match(
    admin: AdminService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    """@username сменил владельца: в базе двое — администратор выбирает по ID (аудит R22)."""
    client.username = "ivan"
    session.add(client)
    session.add(Client(telegram_id=OTHER_TELEGRAM_ID, username="Ivan", first_seen_at=clock.now))
    await session.commit()

    found = await admin.find_clients(ClientQuery(username="ivan"))

    assert sorted(c.telegram_id for c in found) == [TELEGRAM_ID, OTHER_TELEGRAM_ID]


async def test_unknown_client_not_found(admin: AdminService, client: Client) -> None:
    assert await admin.find_clients(ClientQuery(telegram_id=OTHER_TELEGRAM_ID)) == []
    assert await admin.card(OTHER_TELEGRAM_ID) is None


# --- Карточка ---


async def test_card_shows_subscription_payments_trial_and_devices(
    admin: AdminService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await paid_account(remnawave, clock.now + timedelta(days=20))
    remnawave.simulate_device(TELEGRAM_ID, phone("a", clock.now))
    for days, price, paid_ago in ((30, "150", 40), (90, "405", 10)):
        session.add(
            Order(
                client_id=client.id,
                tariff_id=f"d{days}",
                price=Decimal(price),
                days=days,
                status=OrderStatus.ACCESS_GRANTED,
                paid_amount=Decimal(price),
                paid_at=clock.now - timedelta(days=paid_ago),
            )
        )
    session.add(Order(client_id=client.id, tariff_id="d30", price=Decimal(150), days=30))
    await session.commit()

    card = await admin.card(TELEGRAM_ID)

    assert card is not None
    assert card.client.telegram_id == TELEGRAM_ID
    assert card.panel is not None and card.panel.profile is not None
    assert card.panel.profile.status is ProfileStatus.ACTIVE
    assert card.panel.profile.expire_at == clock.now + timedelta(days=20)
    assert card.panel.devices is not None
    assert [d.hwid for d in card.panel.devices.devices] == ["a"]
    assert card.payments.count == 2
    assert card.payments.total == Decimal("555")
    assert [p.tariff_id for p in card.payments.recent] == ["d90", "d30"]  # новые сверху
    assert card.trial.granted_at is None and not card.trial.delivered


async def test_card_shows_delivered_trial(
    admin: AdminService, trials: TrialService, client: Client, clock: FakeClock
) -> None:
    await trials.start(client.id, TELEGRAM_ID)

    card = await admin.card(TELEGRAM_ID)

    assert card is not None
    assert card.trial.granted_at == clock.now and card.trial.delivered
    assert card.panel is not None and card.panel.profile is not None
    assert card.panel.profile.status is ProfileStatus.TRIAL


async def test_card_without_panel_still_shows_database_part(
    admin: AdminService, remnawave: FakeRemnawave, client: Client
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    card = await admin.card(TELEGRAM_ID)

    assert card is not None
    assert card.panel is None  # панель не ответила — карточка всё равно есть


async def test_card_of_client_without_account(admin: AdminService, client: Client) -> None:
    card = await admin.card(TELEGRAM_ID)
    assert card is not None and card.panel is not None
    assert card.panel.profile is None and card.panel.devices is None


# --- Ручная выдача ---


async def test_preview_counts_from_end_of_active_subscription(
    admin: AdminService, remnawave: FakeRemnawave, client: Client, clock: FakeClock
) -> None:
    expire_at = clock.now + timedelta(days=10)
    await paid_account(remnawave, expire_at)

    preview = await admin.preview_extension(TELEGRAM_ID, 30)

    assert preview is not None
    assert preview.current_expire_at == expire_at
    assert preview.new_expire_at == expire_at + timedelta(days=30)
    assert remnawave.writes == [("create", f"tg_{TELEGRAM_ID}")]  # предпросмотр ничего не меняет


async def test_extend_active_subscription_and_journal(
    admin: AdminService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    expire_at = clock.now + timedelta(days=10)
    await paid_account(remnawave, expire_at)

    result = await admin.extend(ADMIN_ID, TELEGRAM_ID, 30, expected_expire_at=expire_at)

    assert result.outcome is ExtensionOutcome.EXTENDED
    assert result.expire_at == expire_at + timedelta(days=30)
    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == result.expire_at
    [event] = await admin_events(session)
    assert (event.admin_id, event.client_id) == (ADMIN_ID, client.id)
    assert event.details == {
        "action": AdminActionKind.EXTEND.value,
        "days": 30,
        "expire_before": expire_at.isoformat(),
        "expire_after": result.expire_at.isoformat(),
    }


async def test_extend_new_client_creates_paid_account(
    admin: AdminService, remnawave: FakeRemnawave, client: Client, clock: FakeClock
) -> None:
    result = await admin.extend(ADMIN_ID, TELEGRAM_ID, 7, expected_expire_at=None)

    assert result.outcome is ExtensionOutcome.EXTENDED
    account = await remnawave.get_account(TELEGRAM_ID)
    assert account.expire_at == clock.now + timedelta(days=7)
    assert account.traffic_limit_bytes == UNLIMITED_TRAFFIC_BYTES
    assert account.device_limit == PAID_DEVICE_LIMIT


async def test_extend_trial_lifts_trial_limits(
    admin: AdminService,
    trials: TrialService,
    remnawave: FakeRemnawave,
    client: Client,
    clock: FakeClock,
) -> None:
    """Ручная выдача — подписка: лимит 7 ГБ снимается, дни добавляются к концу пробного."""
    trial = await trials.start(client.id, TELEGRAM_ID)
    assert trial.account is not None
    remnawave.set_traffic_used(TELEGRAM_ID, TRIAL_TRAFFIC)
    remnawave.set_status(TELEGRAM_ID, AccountStatus.LIMITED)

    await admin.extend(ADMIN_ID, TELEGRAM_ID, 30, expected_expire_at=trial.account.expire_at)

    account = await remnawave.get_account(TELEGRAM_ID)
    assert account.status is AccountStatus.ACTIVE
    assert account.traffic_limit_bytes == UNLIMITED_TRAFFIC_BYTES
    assert account.expire_at == trial.account.expire_at + timedelta(days=30)


async def test_extend_switches_access_back_on_and_ends_grace(
    admin: AdminService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    expired_at = clock.now - timedelta(days=5)
    await paid_account(remnawave, clock.now + timedelta(days=1))
    remnawave.accounts[f"tg_{TELEGRAM_ID}"].expire_at = expired_at
    await remnawave.disable_account(TELEGRAM_ID)
    client.grace_started_at = expired_at
    session.add(client)
    await session.commit()

    await admin.extend(ADMIN_ID, TELEGRAM_ID, 30, expected_expire_at=expired_at)

    account = await remnawave.get_account(TELEGRAM_ID)
    assert account.status is AccountStatus.ACTIVE
    assert account.expire_at == clock.now + timedelta(days=30)
    await session.refresh(client)
    assert client.grace_started_at is None  # как при оплате: новый срок — новый льготный


async def test_double_confirm_extends_once(
    admin: AdminService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    """Двойное нажатие «Да» (или два администратора разом): срок изменился — второе не
    продлевает, а сообщает, что данные устарели."""
    expire_at = clock.now + timedelta(days=10)
    await paid_account(remnawave, expire_at)
    await admin.extend(ADMIN_ID, TELEGRAM_ID, 30, expected_expire_at=expire_at)

    again = await admin.extend(ADMIN_ID, TELEGRAM_ID, 30, expected_expire_at=expire_at)

    assert again.outcome is ExtensionOutcome.CHANGED
    assert again.expire_at == expire_at + timedelta(days=30)
    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == expire_at + timedelta(days=30)
    assert len(await admin_events(session)) == 1


async def test_expected_date_compared_to_the_second(
    admin: AdminService, remnawave: FakeRemnawave, client: Client, clock: FakeClock
) -> None:
    """Кнопка подтверждения несёт срок с точностью до секунды, в панели — миллисекунды."""
    expire_at = clock.now + timedelta(days=10, milliseconds=345)
    await paid_account(remnawave, expire_at)

    result = await admin.extend(
        ADMIN_ID, TELEGRAM_ID, 1, expected_expire_at=expire_at.replace(microsecond=0)
    )

    assert result.outcome is ExtensionOutcome.EXTENDED


async def test_extend_waits_for_payment_crediting_of_same_client(
    admin: AdminService,
    remnawave: FakeRemnawave,
    client: Client,
    clock: FakeClock,
    term_locks: KeyedLocks,
) -> None:
    """Зачёт оплаты держит блокировку срока клиента (по его id в базе): ручная выдача ждёт
    и не считает срок от старой даты — срок изменился, выдача не делается (CHANGED)."""
    expire_at = clock.now + timedelta(days=10)
    await paid_account(remnawave, expire_at)
    paid_until = expire_at + timedelta(days=30)

    async with term_locks.hold(client.id):
        extension = asyncio.create_task(
            admin.extend(ADMIN_ID, TELEGRAM_ID, 7, expected_expire_at=expire_at)
        )
        await asyncio.sleep(0.01)
        assert not extension.done()
        await remnawave.set_expiry(TELEGRAM_ID, paid_until)  # «оплата» под блокировкой

    result = await extension

    assert result.outcome is ExtensionOutcome.CHANGED
    assert (await remnawave.get_account(TELEGRAM_ID)).expire_at == paid_until


@pytest.mark.parametrize("days", [0, MAX_MANUAL_GRANT_DAYS + 1])
async def test_extend_over_limit_refused(
    admin: AdminService, remnawave: FakeRemnawave, client: Client, days: int
) -> None:
    with pytest.raises(GrantDaysOutOfRange):
        await admin.preview_extension(TELEGRAM_ID, days)
    with pytest.raises(GrantDaysOutOfRange):
        await admin.extend(ADMIN_ID, TELEGRAM_ID, days, expected_expire_at=None)
    assert remnawave.writes == []


async def test_extend_unknown_client_refused(
    admin: AdminService, remnawave: FakeRemnawave, client: Client
) -> None:
    """Выдаём только клиентам бота: по чужому ID аккаунт не создаётся."""
    assert await admin.preview_extension(OTHER_TELEGRAM_ID, 7) is None
    result = await admin.extend(ADMIN_ID, OTHER_TELEGRAM_ID, 7, expected_expire_at=None)
    assert result.outcome is ExtensionOutcome.CLIENT_NOT_FOUND
    assert remnawave.writes == []


# --- Сброс пробного ---


async def test_reset_trial_lets_client_take_it_again(
    admin: AdminService,
    trials: TrialService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await trials.start(client.id, TELEGRAM_ID)
    granted_at = clock.now
    clock.advance(timedelta(days=10))

    outcome = await admin.reset_trial(ADMIN_ID, TELEGRAM_ID)

    assert outcome is TrialResetOutcome.RESET
    await session.refresh(client)
    assert client.trial_granted_at is None and client.trial_extensions == 0
    [event] = await admin_events(session)
    assert event.admin_id == ADMIN_ID
    assert event.details == {
        "action": AdminActionKind.TRIAL_RESET.value,
        "trial_granted_at": granted_at.isoformat(),
    }
    renewed = await trials.start(client.id, TELEGRAM_ID)
    assert renewed.outcome is TrialOutcome.GRANTED


async def test_reset_trial_not_taken(admin: AdminService, client: Client) -> None:
    assert await admin.reset_trial(ADMIN_ID, TELEGRAM_ID) is TrialResetOutcome.NOT_USED


async def test_reset_trial_refused_for_paying_client(
    admin: AdminService, trials: TrialService, session: AsyncSession, client: Client
) -> None:
    """Платившему пробный не положен: кнопки у него нет, сброс ничего бы не дал."""
    await trials.start(client.id, TELEGRAM_ID)
    session.add(
        Order(
            client_id=client.id,
            tariff_id="d30",
            price=Decimal(150),
            days=30,
            paid_amount=Decimal(150),
            paid_at=client.first_seen_at,
        )
    )
    await session.commit()

    assert await admin.reset_trial(ADMIN_ID, TELEGRAM_ID) is TrialResetOutcome.HAS_PAID
    await session.refresh(client)
    assert client.trial_granted_at is not None
    assert await admin_events(session) == []


async def test_reset_trial_unknown_client(admin: AdminService) -> None:
    assert await admin.reset_trial(ADMIN_ID, TELEGRAM_ID) is TrialResetOutcome.CLIENT_NOT_FOUND


# --- Устройства ---


async def test_delete_device_of_client(
    admin: AdminService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await paid_account(remnawave, clock.now + timedelta(days=5))
    for hwid in ("a", "b"):
        remnawave.simulate_device(TELEGRAM_ID, phone(hwid, clock.now))

    result = await admin.delete_device(ADMIN_ID, TELEGRAM_ID, device_ref("a"))

    assert result is not None and result.deleted
    assert [d.hwid for d in result.devices.devices] == ["b"]
    [event] = await admin_events(session)
    assert event.details == {
        "action": AdminActionKind.DEVICE_DELETED.value,
        "device": device_ref("a"),
    }


async def test_device_of_another_client_not_deleted(
    admin: AdminService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await paid_account(remnawave, clock.now + timedelta(days=5))
    remnawave.simulate_device(TELEGRAM_ID, phone("a", clock.now))

    result = await admin.delete_device(ADMIN_ID, TELEGRAM_ID, device_ref("чужое"))

    assert result is not None and not result.deleted
    assert len(await remnawave.list_devices(TELEGRAM_ID)) == 1
    assert await admin_events(session) == []


# --- Перевыпуск ссылки ---


async def test_revoke_link(
    admin: AdminService,
    remnawave: FakeRemnawave,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
) -> None:
    await paid_account(remnawave, clock.now + timedelta(days=5))
    before = (await remnawave.get_account(TELEGRAM_ID)).subscription_url

    outcome = await admin.revoke_link(ADMIN_ID, TELEGRAM_ID)

    assert outcome is RevokeOutcome.REVOKED
    assert (await remnawave.get_account(TELEGRAM_ID)).subscription_url != before
    [event] = await admin_events(session)
    assert event.details == {"action": AdminActionKind.LINK_REVOKED.value}


async def test_revoke_without_account(admin: AdminService, client: Client) -> None:
    assert await admin.revoke_link(ADMIN_ID, TELEGRAM_ID) is RevokeOutcome.NO_ACCOUNT
