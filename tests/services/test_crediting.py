"""Зачёт оплаты и выдача доступа (задача 13): ровно один раз, «поставь срок до X»,
повторы при сбое панели, пересчёт при оплате не на сумму тарифа."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.content.loader import ADMIN_TEXTS_FILE, load_texts
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.domain.constants import (
    BYTES_PER_GB,
    GRANT_FAST_RETRIES,
    GRANT_RETRY_DEADLINE,
    PAID_DEVICE_LIMIT,
    TRIAL_DEVICE_LIMIT,
    UNLIMITED_TRAFFIC_BYTES,
)
from tsproxy.domain.grace import GraceStage
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.payments_types import PaymentRequest, PaymentState, PaymentStatus
from tsproxy.integrations.remnawave_types import (
    AccountStatus,
    ProxyAccount,
    RemnawaveRejected,
    RemnawaveUnavailable,
)
from tsproxy.services.account_checks import AccountCheck, CheckSource
from tsproxy.services.admin_topics import AdminTopic
from tsproxy.services.alerts import Alerter
from tsproxy.services.clients import ClientService
from tsproxy.services.crediting import (
    CreditingService,
    GrantedAccess,
    OrderNotFound,
    PaymentMismatch,
    PaymentOutcome,
)
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.grace import GraceHandler
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker

TELEGRAM_ID = 111
ADMIN_CHAT_ID = -100500
ALERTS_THREAD_ID = 7
PAYMENTS_THREAD_ID = 8
PRICE = Decimal("150")
DAYS = 30
MONTH = timedelta(days=DAYS)
ADMIN_TEXTS = Path(__file__).parent.parent.parent / "content" / ADMIN_TEXTS_FILE


class SwitchableRemnawave(FakeRemnawave):
    """Панель, которую тест «роняет» целиком (`down`) или по одному действию."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(RunMode.PROD, now=clock)
        self.down = False
        self.create_error: Exception | None = None
        self.enable_down = False

    def _raise_pending(self) -> None:
        if self.down:
            raise RemnawaveUnavailable("panel down")
        super()._raise_pending()

    async def get_or_create_account(
        self, telegram_id: int, *, expire_at: datetime, device_limit: int, traffic_limit_bytes: int
    ) -> ProxyAccount:
        if self.create_error is not None:
            raise self.create_error
        return await super().get_or_create_account(
            telegram_id,
            expire_at=expire_at,
            device_limit=device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
        )

    async def enable_account(self, telegram_id: int) -> ProxyAccount:
        if self.enable_down:
            raise RemnawaveUnavailable("timeout")
        return await super().enable_account(telegram_id)


@dataclass
class FakePaymentMessages:
    """Сообщения клиенту об оплате — запоминаем вместо Telegram."""

    granted: list[GrantedAccess] = field(default_factory=list)
    pending: list[int] = field(default_factory=list)

    async def access_granted(self, granted: GrantedAccess) -> None:
        self.granted.append(granted)

    async def access_pending(self, telegram_id: int) -> None:
        self.pending.append(telegram_id)


@dataclass
class Setup:
    service: CreditingService
    remnawave: SwitchableRemnawave
    payments: FakePaymentProvider
    messages: FakePaymentMessages
    alerts: FakeAlertSender
    topic: FakeAlertSender
    session_factory: async_sessionmaker[AsyncSession]
    clock: FakeClock


def build(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker
) -> Setup:
    remnawave = SwitchableRemnawave(clock)
    alerts = FakeAlertSender()
    topic = FakeAlertSender()
    messages = FakePaymentMessages()
    admin_texts = load_texts(ADMIN_TEXTS)
    service = CreditingService(
        session_factory,
        remnawave,
        EventLog(clock, mask),
        Alerter(alerts, admin_texts, ADMIN_CHAT_ID, ALERTS_THREAD_ID, clock, mask),
        AdminTopic(topic, ADMIN_CHAT_ID, PAYMENTS_THREAD_ID),
        admin_texts,
        messages,
        clock,
        KeyedLocks(),
        write_retry_delay=timedelta(0),
    )
    return Setup(
        service,
        remnawave,
        FakePaymentProvider(clock),
        messages,
        alerts,
        topic,
        session_factory,
        clock,
    )


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def s(session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker) -> Setup:
    return build(session_factory, clock, mask)


async def paid_order(
    s: Setup,
    client: Client,
    *,
    amount: Decimal | None = None,
    status: OrderStatus = OrderStatus.CREATED,
) -> tuple[int, PaymentState]:
    """Заказ месяца с платежом, который клиент оплатил (`amount` — не по тарифу)."""
    async with s.session_factory() as session:
        order = Order(
            client_id=client.id,
            tariff_id="m1",
            price=PRICE,
            days=DAYS,
            status=status,
            created_at=s.clock(),
        )
        session.add(order)
        await session.flush()
        created = await s.payments.create_payment(
            PaymentRequest(order.id, PRICE, "Подписка", order.idempotency_key)
        )
        order.payment_provider = s.payments.name
        order.payment_id = created.payment_id
        await session.commit()
        order_id = order.id
    s.payments.ledger.pay(created.payment_id, s.clock(), amount)
    return order_id, await s.payments.get_payment(created.payment_id)


async def load_order(s: Setup, order_id: int) -> Order:
    async with s.session_factory() as session:
        return await session.get_one(Order, order_id)


async def load_client(s: Setup, client_id: int) -> Client:
    async with s.session_factory() as session:
        return await session.get_one(Client, client_id)


async def event_kinds(s: Setup) -> list[str]:
    async with s.session_factory() as session:
        return list(await session.scalars(select(Event.kind).order_by(Event.id)))


async def existing_account(
    s: Setup,
    *,
    expire_at: datetime,
    traffic_limit_bytes: int = UNLIMITED_TRAFFIC_BYTES,
    device_limit: int = PAID_DEVICE_LIMIT,
) -> ProxyAccount:
    return await s.remnawave.get_or_create_account(
        TELEGRAM_ID,
        expire_at=expire_at,
        device_limit=device_limit,
        traffic_limit_bytes=traffic_limit_bytes,
    )


async def retry_until_settled(s: Setup, order_id: int, step: timedelta) -> int:
    """Двигать время и запускать фоновую задачу, пока заказ ждёт выдачи. Возвращает число шагов."""
    steps = 0
    while (await load_order(s, order_id)).status is OrderStatus.PAID:
        s.clock.advance(step)
        await s.service.retry_grants()
        steps += 1
        assert steps < 10_000
    return steps


# --- Основной путь ---


async def test_payment_creates_account_and_grants_month(s: Setup, client: Client) -> None:
    order_id, payment = await paid_order(s, client)
    paid_at = s.clock()

    result = await s.service.credit(order_id, payment)

    assert result.outcome is PaymentOutcome.GRANTED
    account = await s.remnawave.get_account(TELEGRAM_ID)
    assert account.expire_at == paid_at + MONTH
    assert account.traffic_limit_bytes == UNLIMITED_TRAFFIC_BYTES
    assert account.device_limit == PAID_DEVICE_LIMIT
    order = await load_order(s, order_id)
    assert order.status is OrderStatus.ACCESS_GRANTED
    assert order.paid_amount == PRICE
    assert order.paid_at == paid_at
    assert order.grant_target_expire_at == paid_at + MONTH
    assert order.next_grant_attempt_at is None
    [granted] = s.messages.granted
    assert (granted.telegram_id, granted.order_id, granted.prorated) == (
        TELEGRAM_ID,
        order_id,
        False,
    )
    assert granted.account.expire_at == paid_at + MONTH
    assert s.messages.pending == []
    assert await event_kinds(s) == [EventKind.PAYMENT_RECEIVED, EventKind.ACCESS_GRANTED]


async def test_admins_see_every_payment_in_payments_topic(s: Setup, client: Client) -> None:
    order_id, payment = await paid_order(s, client)

    await s.service.credit(order_id, payment)

    [notice] = s.topic.sent
    assert (notice.chat_id, notice.thread_id) == (ADMIN_CHAT_ID, PAYMENTS_THREAD_ID)
    assert f"#{order_id}" in notice.text
    assert str(TELEGRAM_ID) in notice.text
    assert "150" in notice.text
    assert s.alerts.sent == []


async def test_client_name_is_escaped_in_admin_notice(s: Setup, session: AsyncSession) -> None:
    client = Client(telegram_id=TELEGRAM_ID, name="<b>Ваня</b>", first_seen_at=s.clock())
    session.add(client)
    await session.commit()
    order_id, payment = await paid_order(s, client)

    await s.service.credit(order_id, payment)

    [notice] = s.topic.sent
    assert "<b>Ваня" not in notice.text
    assert "&lt;b&gt;Ваня" in notice.text


async def test_active_subscription_gets_days_added_to_its_end(s: Setup, client: Client) -> None:
    ends = s.clock() + timedelta(days=5)
    await existing_account(s, expire_at=ends)
    order_id, payment = await paid_order(s, client)

    await s.service.credit(order_id, payment)

    assert (await s.remnawave.get_account(TELEGRAM_ID)).expire_at == ends + MONTH


async def test_purchase_during_trial_lifts_trial_limits(
    s: Setup, client: Client, session: AsyncSession
) -> None:
    client.trial_granted_at = s.clock() - timedelta(days=4)
    await session.commit()
    trial_ends = s.clock() + timedelta(days=3)
    await existing_account(
        s,
        expire_at=trial_ends,
        traffic_limit_bytes=7 * BYTES_PER_GB,
        device_limit=TRIAL_DEVICE_LIMIT,
    )
    s.remnawave.set_status(TELEGRAM_ID, AccountStatus.LIMITED)  # 7 ГБ израсходованы
    order_id, payment = await paid_order(s, client)

    await s.service.credit(order_id, payment)

    account = await s.remnawave.get_account(TELEGRAM_ID)
    assert account.expire_at == trial_ends + MONTH
    assert account.traffic_limit_bytes == UNLIMITED_TRAFFIC_BYTES
    assert account.device_limit == PAID_DEVICE_LIMIT
    assert account.status is AccountStatus.ACTIVE
    # оплативший больше не видит кнопку пробного
    clients = ClientService(s.session_factory, s.clock)
    assert not await clients.trial_offered(await load_client(s, client.id))


async def test_purchase_in_grace_counts_from_payment_and_ends_grace(
    s: Setup, client: Client, session: AsyncSession
) -> None:
    client.grace_started_at = s.clock() - timedelta(days=1)
    await session.commit()
    # в льготный период срок в панели уже сдвинут до конца льготного
    await existing_account(s, expire_at=s.clock() + timedelta(days=3))
    order_id, payment = await paid_order(s, client)
    paid_at = s.clock()

    await s.service.credit(order_id, payment)

    assert (await s.remnawave.get_account(TELEGRAM_ID)).expire_at == paid_at + MONTH
    assert (await load_client(s, client.id)).grace_started_at is None


async def test_access_disabled_after_grace_is_switched_back_on(
    s: Setup, client: Client, session: AsyncSession
) -> None:
    # льготный кончился: бот выключил доступ, аккаунт и отметка льготного остались
    client.grace_started_at = s.clock() - timedelta(days=6)
    await session.commit()
    await existing_account(s, expire_at=s.clock() + timedelta(hours=1))
    s.remnawave.set_status(TELEGRAM_ID, AccountStatus.DISABLED)
    order_id, payment = await paid_order(s, client)
    paid_at = s.clock()

    await s.service.credit(order_id, payment)

    account = await s.remnawave.get_account(TELEGRAM_ID)
    assert account.status is AccountStatus.ACTIVE
    assert account.expire_at == paid_at + MONTH
    assert (await load_client(s, client.id)).grace_started_at is None


@dataclass
class RecordingGraceMessenger:
    stages: list[GraceStage] = field(default_factory=list)

    async def send_grace_notice(
        self, telegram_id: int, stage: GraceStage, access_until: datetime
    ) -> bool:
        self.stages.append(stage)
        return True


async def grace_check(handler: GraceHandler, s: Setup) -> None:
    account = await s.remnawave.get_account(TELEGRAM_ID)
    await handler.handle(
        AccountCheck(TELEGRAM_ID, account, CheckSource.RECONCILIATION, None, s.clock())
    )


async def test_payment_in_grace_ends_grace_at_once_even_while_panel_is_down(
    s: Setup, client: Client, mask: Masker
) -> None:
    grace_messenger = RecordingGraceMessenger()
    grace = GraceHandler(
        s.session_factory, s.remnawave, EventLog(s.clock, mask), grace_messenger, s.clock
    )
    paid_until = s.clock() + timedelta(hours=1)
    await existing_account(s, expire_at=paid_until)
    s.clock.now = paid_until
    s.remnawave.set_status(TELEGRAM_ID, AccountStatus.EXPIRED)
    await grace_check(grace, s)  # срок кончился — льготный начался (задача 11)
    assert grace_messenger.stages == [GraceStage.STARTED]

    s.clock.advance(timedelta(days=1))
    s.remnawave.down = True
    order_id, payment = await paid_order(s, client)
    paid_at = s.clock()
    assert (await s.service.credit(order_id, payment)).outcome is PaymentOutcome.PENDING

    # оплатил — льготный кончился сразу, хотя доступ ещё выдаётся
    assert (await load_client(s, client.id)).grace_started_at is None
    s.remnawave.down = False
    await grace_check(grace, s)
    assert grace_messenger.stages == [GraceStage.STARTED]  # без «продлите подписку»

    s.clock.advance(timedelta(minutes=1))
    await s.service.retry_grants()

    # новый срок — от момента оплаты, не от конца льготного: льготные дни — подарок
    account = await s.remnawave.get_account(TELEGRAM_ID)
    assert account.expire_at == s.clock() + MONTH
    assert s.clock() == paid_at + timedelta(minutes=1)
    await grace_check(grace, s)
    assert grace_messenger.stages == [GraceStage.STARTED]


async def test_order_with_expired_link_is_still_credited(s: Setup, client: Client) -> None:
    order_id, payment = await paid_order(s, client, status=OrderStatus.CANCELED)

    result = await s.service.credit(order_id, payment)

    assert result.outcome is PaymentOutcome.GRANTED


async def test_foreign_account_with_same_telegram_id_is_not_touched(
    s: Setup, client: Client
) -> None:
    foreign = s.remnawave.add_foreign_account("ivan_manual", telegram_id=TELEGRAM_ID)
    order_id, payment = await paid_order(s, client)

    await s.service.credit(order_id, payment)

    assert s.remnawave.accounts["ivan_manual"].snapshot() == foreign
    assert all(username != "ivan_manual" for _, username in s.remnawave.writes)


# --- Ровно один раз ---


async def test_second_credit_of_same_order_changes_nothing(s: Setup, client: Client) -> None:
    order_id, payment = await paid_order(s, client)
    await s.service.credit(order_id, payment)
    s.clock.advance(timedelta(minutes=5))

    again = await s.service.credit(order_id, payment)

    assert again.outcome is PaymentOutcome.ALREADY_CREDITED
    account = await s.remnawave.get_account(TELEGRAM_ID)
    assert account.expire_at == (await load_order(s, order_id)).grant_target_expire_at
    assert len(s.messages.granted) == 1
    assert len(s.topic.sent) == 1
    assert (await event_kinds(s)).count(EventKind.PAYMENT_RECEIVED) == 1


async def test_simultaneous_credits_count_once(s: Setup, client: Client) -> None:
    order_id, payment = await paid_order(s, client)
    paid_at = s.clock()

    results = await asyncio.gather(
        s.service.credit(order_id, payment), s.service.credit(order_id, payment)
    )

    assert sorted(r.outcome for r in results) == sorted(
        [PaymentOutcome.GRANTED, PaymentOutcome.ALREADY_CREDITED]
    )
    assert (await s.remnawave.get_account(TELEGRAM_ID)).expire_at == paid_at + MONTH
    assert len(s.messages.granted) == 1


async def test_two_paid_orders_of_one_client_both_count(s: Setup, client: Client) -> None:
    first_id, first = await paid_order(s, client)
    second_id, second = await paid_order(s, client)
    paid_at = s.clock()

    await asyncio.gather(s.service.credit(first_id, first), s.service.credit(second_id, second))

    assert (await s.remnawave.get_account(TELEGRAM_ID)).expire_at == paid_at + 2 * MONTH


# --- Проверка, что платёж — этого заказа ---


@pytest.mark.parametrize(
    "change",
    [
        {"order_id": 999},
        {"payment_id": "fake_other"},
        {"status": PaymentStatus.PENDING},
        {"currency": "USD"},
        {"amount": Decimal(0)},
    ],
)
async def test_payment_not_matching_order_is_refused(
    s: Setup, client: Client, change: dict[str, Any]
) -> None:
    order_id, payment = await paid_order(s, client)
    wrong = replace(payment, **change)

    with pytest.raises(PaymentMismatch):
        await s.service.credit(order_id, wrong)

    order = await load_order(s, order_id)
    assert order.status is OrderStatus.CREATED
    assert order.paid_at is None
    assert s.remnawave.writes == []


async def test_unknown_order_is_refused(s: Setup, client: Client) -> None:
    _, payment = await paid_order(s, client)

    with pytest.raises(OrderNotFound):
        await s.service.credit(12345, payment)


# --- Оплата не на сумму тарифа (задача 04 п. 8) ---


async def test_other_amount_is_prorated_with_apology_and_admin_notice(
    s: Setup, client: Client
) -> None:
    order_id, payment = await paid_order(s, client, amount=Decimal("75"))
    paid_at = s.clock()

    result = await s.service.credit(order_id, payment)

    assert result.outcome is PaymentOutcome.GRANTED
    # 75 ₽ из 150 ₽ за 30 дней — 15 дней (360 часов) и ещё 2 часа сверху
    expected = paid_at + timedelta(hours=362)
    assert (await s.remnawave.get_account(TELEGRAM_ID)).expire_at == expected
    order = await load_order(s, order_id)
    assert order.paid_amount == Decimal("75")
    assert order.grant_target_expire_at == expected
    [granted] = s.messages.granted
    assert granted.prorated
    [notice] = s.topic.sent
    assert "75" in notice.text and "150" in notice.text
    [alert] = s.alerts.sent
    assert f"#{order_id}" in alert.text


# --- Панель не ответила после оплаты ---


async def test_panel_down_keeps_order_paid_and_retries_until_granted(
    s: Setup, client: Client
) -> None:
    s.remnawave.down = True
    order_id, payment = await paid_order(s, client)
    paid_at = s.clock()

    result = await s.service.credit(order_id, payment)

    assert result.outcome is PaymentOutcome.PENDING
    assert s.messages.pending == [TELEGRAM_ID]
    order = await load_order(s, order_id)
    assert order.status is OrderStatus.PAID
    assert order.paid_at == paid_at
    assert order.grant_attempts == 1
    assert order.next_grant_attempt_at == paid_at + timedelta(minutes=1)

    await s.service.retry_grants()  # ещё рано — попытку не тратим
    assert (await load_order(s, order_id)).grant_attempts == 1

    s.clock.advance(timedelta(minutes=1))
    await s.service.retry_grants()
    assert (await load_order(s, order_id)).grant_attempts == 2

    s.remnawave.down = False
    s.clock.advance(timedelta(minutes=1))
    await s.service.retry_grants()

    order = await load_order(s, order_id)
    assert order.status is OrderStatus.ACCESS_GRANTED
    # срок считается, когда панель впервые ответила: минуты ожидания клиент не теряет
    assert order.grant_target_expire_at == s.clock() + MONTH
    [granted] = s.messages.granted
    assert granted.account.expire_at == s.clock() + MONTH
    assert s.alerts.sent == []


async def test_retry_reuses_saved_target_and_never_adds_days_twice(
    s: Setup, client: Client
) -> None:
    await existing_account(s, expire_at=s.clock() + timedelta(days=2))
    s.remnawave.set_status(TELEGRAM_ID, AccountStatus.DISABLED)
    s.remnawave.enable_down = True  # срок поставлен, а включить аккаунт не вышло
    order_id, payment = await paid_order(s, client)
    target = s.clock() + MONTH

    assert (await s.service.credit(order_id, payment)).outcome is PaymentOutcome.PENDING
    assert (await load_order(s, order_id)).grant_target_expire_at == target

    s.remnawave.enable_down = False
    s.clock.advance(timedelta(minutes=1))
    await s.service.retry_grants()

    account = await s.remnawave.get_account(TELEGRAM_ID)
    assert account.expire_at == target
    assert account.status is AccountStatus.ACTIVE
    assert [action for action, _ in s.remnawave.writes].count("set_expiry") == 1


async def test_alert_after_half_hour_and_give_up_after_a_day(s: Setup, client: Client) -> None:
    s.remnawave.down = True
    order_id, payment = await paid_order(s, client)
    paid_at = s.clock()
    await s.service.credit(order_id, payment)

    for _ in range(GRANT_FAST_RETRIES - 1):
        s.clock.advance(timedelta(minutes=1))
        await s.service.retry_grants()
    assert s.alerts.sent == []  # первые полчаса — без тревоги

    s.clock.advance(timedelta(minutes=1))
    await s.service.retry_grants()
    [delayed] = s.alerts.sent
    assert s.clock() == paid_at + timedelta(minutes=30)
    assert f"#{order_id}" in delayed.text

    await retry_until_settled(s, order_id, timedelta(minutes=10))

    order = await load_order(s, order_id)
    assert order.status is OrderStatus.ACCESS_FAILED
    assert order.next_grant_attempt_at is None
    assert s.clock() >= paid_at + GRANT_RETRY_DEADLINE
    assert s.clock() < paid_at + GRANT_RETRY_DEADLINE + timedelta(minutes=10)
    assert len(s.alerts.sent) == 2
    assert f"#{order_id}" in s.alerts.sent[-1].text
    assert EventKind.ACCESS_FAILED in await event_kinds(s)
    assert s.messages.granted == []


async def test_panel_rejection_sends_order_to_manual_review(s: Setup, client: Client) -> None:
    s.remnawave.create_error = RemnawaveRejected("bad request", status_code=400, code=None)
    order_id, payment = await paid_order(s, client)

    result = await s.service.credit(order_id, payment)

    assert result.outcome is PaymentOutcome.MANUAL_REVIEW
    order = await load_order(s, order_id)
    assert order.status is OrderStatus.MANUAL_REVIEW
    assert order.next_grant_attempt_at is None
    assert s.messages.pending == [TELEGRAM_ID]
    [alert] = s.alerts.sent
    assert f"#{order_id}" in alert.text
    s.clock.advance(timedelta(minutes=1))
    await s.service.retry_grants()  # ручная проверка — без автоматических повторов
    assert len(s.alerts.sent) == 1


async def test_later_order_waits_for_earlier_one(s: Setup, client: Client) -> None:
    s.remnawave.down = True
    first_id, first = await paid_order(s, client)
    await s.service.credit(first_id, first)
    second_id, second = await paid_order(s, client)

    result = await s.service.credit(second_id, second)

    assert result.outcome is PaymentOutcome.PENDING
    assert (await load_order(s, first_id)).grant_attempts == 1  # первый не дёргали раньше срока

    s.remnawave.down = False
    s.clock.advance(timedelta(minutes=1))
    await s.service.retry_grants()

    assert (await s.remnawave.get_account(TELEGRAM_ID)).expire_at == s.clock() + 2 * MONTH
    assert [g.order_id for g in s.messages.granted] == [first_id, second_id]


# --- Оплата не записалась в базу ---


def flaky_commits(
    factory: async_sessionmaker[AsyncSession], failures: int
) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий, у которой первые `failures` коммитов падают («база занята»)."""
    left = [failures]

    def make() -> AsyncSession:
        session = factory()
        commit = session.commit

        async def flaky_commit() -> None:
            if left[0] > 0:
                left[0] -= 1
                raise OperationalError("UPDATE orders", {}, Exception("database is locked"))
            await commit()

        session.commit = flaky_commit  # type: ignore[method-assign]
        return session

    return make  # type: ignore[return-value]


async def test_failed_write_is_retried_without_alert(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    s = build(flaky_commits(session_factory, failures=2), clock, mask)
    s.session_factory = session_factory
    order_id, payment = await paid_order(s, client)

    result = await s.service.credit(order_id, payment)

    assert result.outcome is PaymentOutcome.GRANTED
    assert s.alerts.sent == []


async def test_payment_never_recorded_raises_alert(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    s = build(flaky_commits(session_factory, failures=100), clock, mask)
    s.session_factory = session_factory
    order_id, payment = await paid_order(s, client)

    with pytest.raises(OperationalError):
        await s.service.credit(order_id, payment)

    [alert] = s.alerts.sent
    assert f"#{order_id}" in alert.text
    assert payment.payment_id in alert.text
    order = await load_order(s, order_id)
    assert order.status is OrderStatus.CREATED
    assert s.remnawave.writes == []
