"""Возвраты (задача 21): расчёт и заявка, решение администратора один раз, возврат через
платёжку, укорачивание срока с повторами; случайная вторая оплата."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.content.loader import ADMIN_TEXTS_FILE, load_texts
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus, RefundStatus
from tsproxy.db.models import Client, Event, Order, RefundRequest
from tsproxy.domain.constants import PAID_DEVICE_LIMIT, UNLIMITED_TRAFFIC_BYTES
from tsproxy.domain.refund import REFUND_END_MARGIN, RefundKind
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.payments_types import (
    PaymentRejected,
    PaymentRequest,
    PaymentUnavailable,
)
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    ProxyAccount,
    RemnawaveUnavailable,
)
from tsproxy.services.alerts import Alerter
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.refunds import (
    ApprovalOutcome,
    RefundCard,
    RefundQuote,
    RefundRefusal,
    RefundRefused,
    RefundRejection,
    RefundService,
    RejectionOutcome,
)

TELEGRAM_ID = 111
ADMIN_ID = 900
OTHER_ADMIN_ID = 901
ADMIN_CHAT_ID = -100500
ALERTS_THREAD_ID = 7
PRICE = Decimal("150")
DAYS = 30
ADMIN_TEXTS = Path(__file__).parent.parent.parent / "content" / ADMIN_TEXTS_FILE


class FlakyRemnawave(FakeRemnawave):
    """Панель, которую тест роняет; `expiry_times_out` — срок ставится, а ответ теряется."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(RunMode.PROD, now=clock)
        self.down = False
        self.expiry_times_out = False

    def _raise_pending(self) -> None:
        if self.down:
            raise RemnawaveUnavailable("panel down")
        super()._raise_pending()

    async def set_expiry(self, telegram_id: int, expire_at: datetime) -> ProxyAccount:
        account = await super().set_expiry(telegram_id, expire_at)
        if self.expiry_times_out:
            self.expiry_times_out = False
            raise RemnawaveUnavailable("timeout after write")
        return account


@dataclass
class FakeBoard:
    cards: list[RefundCard] = field(default_factory=list)
    error: Exception | None = None

    async def request_created(self, card: RefundCard) -> None:
        if self.error is not None:
            raise self.error
        self.cards.append(card)


@dataclass
class FakeRefundMessages:
    completed: list[tuple[int, Decimal, datetime | None]] = field(default_factory=list)
    rejected: list[tuple[int, RefundRejection]] = field(default_factory=list)

    async def refund_completed(
        self, telegram_id: int, amount: Decimal, expire_at: datetime | None
    ) -> None:
        self.completed.append((telegram_id, amount, expire_at))

    async def refund_rejected(self, telegram_id: int, reason: RefundRejection) -> None:
        self.rejected.append((telegram_id, reason))


@dataclass
class Setup:
    service: RefundService
    remnawave: FlakyRemnawave
    payments: FakePaymentProvider
    board: FakeBoard
    messages: FakeRefundMessages
    alerts: FakeAlertSender
    session_factory: async_sessionmaker[AsyncSession]
    clock: FakeClock


def build(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    *,
    payments_on: bool = True,
) -> Setup:
    remnawave = FlakyRemnawave(clock)
    payments = FakePaymentProvider(clock)
    board = FakeBoard()
    messages = FakeRefundMessages()
    alerts = FakeAlertSender()
    admin_texts = load_texts(ADMIN_TEXTS)
    service = RefundService(
        session_factory,
        remnawave,
        payments if payments_on else None,
        EventLog(clock, mask),
        Alerter(alerts, admin_texts, ADMIN_CHAT_ID, ALERTS_THREAD_ID, clock, mask),
        admin_texts,
        board,
        messages,
        clock,
        KeyedLocks(),
    )
    return Setup(service, remnawave, payments, board, messages, alerts, session_factory, clock)


@pytest.fixture
def setup(engine: AsyncEngine, clock: FakeClock, mask: Masker) -> Setup:
    return build(create_session_factory(engine), clock, mask)


async def paid_order(
    setup: Setup,
    client: Client,
    *,
    paid_at: datetime,
    expire_at: datetime,
    days: int = DAYS,
    price: Decimal = PRICE,
) -> Order:
    """Оплаченный заказ с выданным доступом: платёж в поддельной платёжке, срок в панели."""
    async with setup.session_factory() as session:
        order = Order(client_id=client.id, tariff_id=f"m{days}", price=price, days=days)
        session.add(order)
        await session.flush()
        created = await setup.payments.create_payment(
            PaymentRequest(order.id, price, "Подписка", order.idempotency_key)
        )
        setup.payments.ledger.pay(created.payment_id, paid_at)
        order.payment_provider = setup.payments.name
        order.payment_id = created.payment_id
        order.status = OrderStatus.ACCESS_GRANTED
        order.paid_at = paid_at
        order.paid_amount = price
        order.grant_target_expire_at = expire_at
        await session.commit()
    remnawave = setup.remnawave
    try:
        await remnawave.get_account(client.telegram_id)
    except AccountNotFound:
        await remnawave.get_or_create_account(
            client.telegram_id,
            expire_at=expire_at,
            device_limit=PAID_DEVICE_LIMIT,
            traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES,
        )
    else:
        await remnawave.set_expiry(client.telegram_id, expire_at)
    return order


async def mark_double_payment(setup: Setup, client: Client, order: Order) -> None:
    """Так задача 14 отмечает случайную вторую оплату."""
    async with setup.session_factory() as session:
        session.add(
            Event(
                kind=EventKind.DOUBLE_PAYMENT_SUSPECTED.value,
                client_id=client.id,
                order_id=order.id,
                occurred_at=setup.clock(),
            )
        )
        await session.commit()


async def statuses(setup: Setup) -> list[RefundStatus]:
    async with setup.session_factory() as session:
        return list(await session.scalars(select(RefundRequest.status).order_by(RefundRequest.id)))


async def expire_of(setup: Setup) -> datetime:
    return (await setup.remnawave.get_account(TELEGRAM_ID)).expire_at


async def requested(setup: Setup, client: Client) -> RefundCard:
    card = await setup.service.request(client)
    assert isinstance(card, RefundCard)
    return card


# --- Расчёт и заявка ---


async def test_quote_counts_unused_full_days_of_last_payment(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    paid_at = clock() - timedelta(days=3, hours=1)
    order = await paid_order(setup, client, paid_at=paid_at, expire_at=paid_at + timedelta(days=30))

    quote = await setup.service.quote(client)

    assert quote == RefundQuote(
        order.id, RefundKind.STANDARD, paid_at, PRICE, unused_days=26, amount=Decimal("130.00")
    )


async def test_request_is_saved_logged_and_posted_once(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))

    card = await requested(setup, client)
    again = await setup.service.request(client)

    assert again == RefundRefused(RefundRefusal.ALREADY_REQUESTED)
    assert setup.board.cards == [card]
    assert card.amount == Decimal("150.00")
    assert await statuses(setup) == [RefundStatus.REQUESTED]
    async with setup.session_factory() as session:
        kinds = list(await session.scalars(select(Event.kind)))
    assert kinds.count(EventKind.REFUND_REQUESTED.value) == 1


async def test_two_simultaneous_requests_make_one(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))

    results = await asyncio.gather(setup.service.request(client), setup.service.request(client))

    assert sum(isinstance(r, RefundCard) for r in results) == 1
    assert await statuses(setup) == [RefundStatus.REQUESTED]


async def test_request_not_lost_when_topic_is_unreachable(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    setup.board.error = RuntimeError("telegram down")

    card = await requested(setup, client)

    assert [c.request_id for c in await setup.service.open_requests()] == [card.request_id]
    assert "/refunds" in setup.alerts.sent[0].text


async def test_no_refund_through_bot_when_payments_are_off(
    engine: AsyncEngine, clock: FakeClock, mask: Masker, client: Client
) -> None:
    setup = build(create_session_factory(engine), clock, mask, payments_on=False)

    assert await setup.service.quote(client) == RefundRefused(RefundRefusal.PAYMENTS_OFF)


async def test_no_paid_order_no_refund(setup: Setup, client: Client) -> None:
    assert await setup.service.quote(client) == RefundRefused(RefundRefusal.NO_PAID_ORDER)


async def test_refund_only_within_30_days_of_payment(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    paid_at = clock() - timedelta(days=30, seconds=1)
    await paid_order(setup, client, paid_at=paid_at, expire_at=paid_at + timedelta(days=90))

    assert await setup.service.quote(client) == RefundRefused(RefundRefusal.WINDOW_PASSED)


async def test_second_refund_within_half_a_year_is_refused(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    first = await requested(setup, client)
    await setup.service.approve(ADMIN_ID, first.request_id)
    decided_at = clock()
    clock.advance(timedelta(days=60))
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))

    quote = await setup.service.quote(client)

    assert quote == RefundRefused(
        RefundRefusal.TOO_FREQUENT, datetime(2027, 4, 24, 12, 0, tzinfo=decided_at.tzinfo)
    )


async def test_refund_limited_by_what_is_left_of_the_term(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    # срок укоротили мимо этого заказа (например, перенос подписки)
    await setup.remnawave.set_expiry(TELEGRAM_ID, clock() + timedelta(days=10, hours=2))

    quote = await setup.service.quote(client)

    assert isinstance(quote, RefundQuote)
    assert (quote.unused_days, quote.amount) == (10, Decimal("50.00"))


async def test_disabled_access_has_nothing_to_refund(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    await setup.remnawave.disable_account(TELEGRAM_ID)

    assert await setup.service.quote(client) == RefundRefused(RefundRefusal.NOTHING_UNUSED)


# --- Решение администратора ---


async def test_approve_returns_money_and_shortens_term(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    paid_at = clock() - timedelta(days=10)
    order = await paid_order(setup, client, paid_at=paid_at, expire_at=paid_at + timedelta(days=30))
    # после этого заказа куплен ещё месяц: его дни остаются (Г5)
    await setup.remnawave.set_expiry(TELEGRAM_ID, paid_at + timedelta(days=60))
    card = await requested(setup, client)

    result = await setup.service.approve(ADMIN_ID, card.request_id)

    assert result.outcome is ApprovalOutcome.COMPLETED
    assert result.expire_at == paid_at + timedelta(days=40)
    assert await expire_of(setup) == paid_at + timedelta(days=40)
    [payment] = setup.payments.ledger.find_by_order(order.id)
    assert [r.amount for r in payment.refunds] == [Decimal("100.00")]
    assert setup.messages.completed == [(TELEGRAM_ID, Decimal("100.00"), result.expire_at)]
    assert await statuses(setup) == [RefundStatus.COMPLETED]
    async with setup.session_factory() as session:
        assert (await session.get_one(Order, order.id)).status is OrderStatus.REFUNDED
        admin_events = list(
            await session.scalars(select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value))
        )
    assert [(e.admin_id, (e.details or {})["action"]) for e in admin_events] == [
        (ADMIN_ID, "refund_approved")
    ]


async def test_refund_of_the_only_order_ends_access_without_grace(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    paid_at = clock() - timedelta(days=10, hours=6)
    await paid_order(setup, client, paid_at=paid_at, expire_at=paid_at + timedelta(days=30))
    card = await requested(setup, client)

    result = await setup.service.approve(ADMIN_ID, card.request_id)

    account = await setup.remnawave.get_account(TELEGRAM_ID)
    assert account.status is AccountStatus.DISABLED
    assert account.expire_at == clock() + REFUND_END_MARGIN
    assert result.expire_at is None
    assert setup.messages.completed == [(TELEGRAM_ID, card.amount, None)]


async def test_double_approve_refunds_once(setup: Setup, client: Client, clock: FakeClock) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    card = await requested(setup, client)

    results = await asyncio.gather(
        setup.service.approve(ADMIN_ID, card.request_id),
        setup.service.approve(OTHER_ADMIN_ID, card.request_id),
    )

    assert sorted(r.outcome for r in results) == [
        ApprovalOutcome.ALREADY_DECIDED,
        ApprovalOutcome.COMPLETED,
    ]
    [payment] = setup.payments.ledger.find_by_order(order.id)
    assert len(payment.refunds) == 1
    assert len(setup.messages.completed) == 1


async def test_provider_unavailable_retries_with_same_key(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    card = await requested(setup, client)
    setup.payments.fail_next(PaymentUnavailable("timeout"))

    result = await setup.service.approve(ADMIN_ID, card.request_id)
    assert result.outcome is ApprovalOutcome.PROVIDER_PENDING
    assert setup.alerts.sent
    await setup.service.retry_unfinished()
    await setup.service.retry_unfinished()

    [payment] = setup.payments.ledger.find_by_order(order.id)
    assert len(payment.refunds) == 1
    assert await statuses(setup) == [RefundStatus.COMPLETED]


async def test_provider_rejection_stops_and_alerts(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    expire_at = clock() + timedelta(days=30)
    await paid_order(setup, client, paid_at=clock(), expire_at=expire_at)
    card = await requested(setup, client)
    setup.payments.fail_next(PaymentRejected("no"))

    result = await setup.service.approve(ADMIN_ID, card.request_id)
    await setup.service.retry_unfinished()

    assert result.outcome is ApprovalOutcome.PROVIDER_REJECTED
    assert await statuses(setup) == [RefundStatus.FAILED]
    assert await expire_of(setup) == expire_at
    assert any("отказала" in m.text for m in setup.alerts.sent)


async def test_money_returned_but_panel_down_alerts_and_retries(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    paid_at = clock() - timedelta(days=10)
    await paid_order(setup, client, paid_at=paid_at, expire_at=paid_at + timedelta(days=60))
    card = await requested(setup, client)
    setup.remnawave.down = True

    result = await setup.service.approve(ADMIN_ID, card.request_id)

    assert result.outcome is ApprovalOutcome.TERM_PENDING
    assert await statuses(setup) == [RefundStatus.REFUNDED]
    assert any("срок не укорочен" in m.text for m in setup.alerts.sent)
    assert setup.messages.completed == []

    setup.remnawave.down = False
    await setup.service.retry_unfinished()

    assert await statuses(setup) == [RefundStatus.COMPLETED]
    assert await expire_of(setup) == paid_at + timedelta(days=60) - timedelta(days=card.unused_days)
    assert len(setup.messages.completed) == 1


async def test_lost_panel_answer_does_not_shorten_twice(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    paid_at = clock() - timedelta(days=10)
    await paid_order(setup, client, paid_at=paid_at, expire_at=paid_at + timedelta(days=60))
    card = await requested(setup, client)
    setup.remnawave.expiry_times_out = True

    await setup.service.approve(ADMIN_ID, card.request_id)
    await setup.service.retry_unfinished()

    assert await expire_of(setup) == paid_at + timedelta(days=60) - timedelta(days=card.unused_days)
    assert await statuses(setup) == [RefundStatus.COMPLETED]


async def test_reject_for_provider_block_tells_client(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    expire_at = clock() + timedelta(days=30)
    order = await paid_order(setup, client, paid_at=clock(), expire_at=expire_at)
    card = await requested(setup, client)

    result = await setup.service.reject(ADMIN_ID, card.request_id, RefundRejection.PROVIDER_BLOCK)
    late = await setup.service.approve(OTHER_ADMIN_ID, card.request_id)

    assert result.outcome is RejectionOutcome.REJECTED
    assert late.outcome is ApprovalOutcome.ALREADY_DECIDED
    assert setup.messages.rejected == [(TELEGRAM_ID, RefundRejection.PROVIDER_BLOCK)]
    [payment] = setup.payments.ledger.find_by_order(order.id)
    assert payment.refunds == ()
    assert await expire_of(setup) == expire_at
    async with setup.session_factory() as session:
        row = await session.get_one(RefundRequest, card.request_id)
    assert (row.status, row.decision_reason, row.decided_by_admin_id) == (
        RefundStatus.REJECTED,
        "provider_block",
        ADMIN_ID,
    )


async def test_rejected_request_does_not_block_a_new_one(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    card = await requested(setup, client)
    await setup.service.reject(ADMIN_ID, card.request_id, RefundRejection.OTHER)

    assert isinstance(await setup.service.quote(client), RefundQuote)


# --- Случайная вторая оплата ---


async def test_double_payment_full_refund_outside_general_rules(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    first = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    second = await paid_order(
        setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=60)
    )
    await mark_double_payment(setup, client, second)

    card = await setup.service.request_double_payment(client, second.id)
    assert isinstance(card, RefundCard)
    assert (card.kind, card.amount, card.unused_days) == (
        RefundKind.DOUBLE_PAYMENT,
        Decimal("150.00"),
        30,
    )
    await setup.service.approve(ADMIN_ID, card.request_id)
    assert await expire_of(setup) == clock() + timedelta(days=30)

    # не считается «прошлым возвратом»: обычный возврат первой оплаты остаётся возможным
    quote = await setup.service.quote(client)
    assert isinstance(quote, RefundQuote)
    assert quote.order_id == first.id


async def test_double_payment_needs_the_mark_and_seven_days(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))

    unmarked = await setup.service.quote_double_payment(client, order.id)
    await mark_double_payment(setup, client, order)
    clock.advance(timedelta(days=7, seconds=1))
    late = await setup.service.quote_double_payment(client, order.id)

    assert unmarked == RefundRefused(RefundRefusal.NOT_DOUBLE_PAYMENT)
    assert late == RefundRefused(RefundRefusal.WINDOW_PASSED)


async def test_double_payment_of_someone_else_is_not_found(
    setup: Setup, client: Client, session: AsyncSession, clock: FakeClock
) -> None:
    other = Client(telegram_id=222, first_seen_at=clock())
    session.add(other)
    await session.commit()
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + timedelta(days=30))
    await mark_double_payment(setup, client, order)

    quote = await setup.service.quote_double_payment(other, order.id)

    assert quote == RefundRefused(RefundRefusal.NO_PAID_ORDER)
