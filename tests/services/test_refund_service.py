"""Возвраты (задача 21, решение владельца 2026-10-30): администратор возвращает оплату
за последние 30 дней кнопкой в карточке клиента; клиент сам возвращает случайную вторую
оплату в течение 7 дней. Вся сумма — через платёжку, со срока снимается всё, что дала
эта оплата; повторный возврат той же оплаты невозможен."""

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
from tsproxy.domain.refund import REFUND_END_MARGIN
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.payments_types import (
    PaidPayment,
    PaymentRefunds,
    PaymentRejected,
    PaymentRequest,
    PaymentUnavailable,
    Refund,
    RefundOutcome,
)
from tsproxy.integrations.refunds import ProviderRefunds, RefundRouter
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    ProxyAccount,
    RemnawaveUnavailable,
)
from tsproxy.integrations.telegram_stars import STARS_PROVIDER
from tsproxy.services.admin import AdminActionKind
from tsproxy.services.admin_topics import AdminTopic
from tsproxy.services.alerts import Alerter
from tsproxy.services.crediting import DOUBLE_DETAIL, STARS_DETAIL
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.refunds import (
    AdminRef,
    RefundablePayment,
    RefundKind,
    RefundProgress,
    RefundRefusal,
    RefundRefused,
    RefundResult,
    RefundService,
)

TELEGRAM_ID = 111
OTHER_ID = 222
ADMIN = AdminRef(900, "Саша")
ADMIN_CHAT_ID = -100500
ALERTS_THREAD_ID = 7
PAYMENTS_THREAD_ID = 4
PRICE = Decimal("150")
DAYS = 30
MONTH = timedelta(days=DAYS)
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
class FakeRefundMessages:
    completed: list[tuple[int, Decimal, datetime | None]] = field(default_factory=list)
    stars: list[int | None] = field(default_factory=list)  # задача 33: чем платили

    async def refund_completed(
        self, telegram_id: int, amount: Decimal, expire_at: datetime | None, *, stars: int | None
    ) -> None:
        self.completed.append((telegram_id, amount, expire_at))
        self.stars.append(stars)


@dataclass
class Setup:
    service: RefundService
    remnawave: FlakyRemnawave
    payments: FakePaymentProvider
    messages: FakeRefundMessages
    alerts: FakeAlertSender
    topic: FakeAlertSender
    session_factory: async_sessionmaker[AsyncSession]
    clock: FakeClock

    def topic_texts(self) -> list[str]:
        return [m.text for m in self.topic.sent]


def build(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    *,
    payments_on: bool = True,
    stars: PaymentRefunds | None = None,  # задача 33: возврат звёзд
) -> Setup:
    remnawave = FlakyRemnawave(clock)
    payments = FakePaymentProvider(clock)
    messages = FakeRefundMessages()
    alerts = FakeAlertSender()
    topic = FakeAlertSender()
    admin_texts = load_texts(ADMIN_TEXTS)
    service = RefundService(
        session_factory,
        remnawave,
        RefundRouter(
            ({payments.name: ProviderRefunds(payments)} if payments_on else {})
            | ({} if stars is None else {STARS_PROVIDER: stars})
        ),
        EventLog(clock, mask),
        Alerter(alerts, admin_texts, ADMIN_CHAT_ID, ALERTS_THREAD_ID, clock, mask),
        AdminTopic(topic, ADMIN_CHAT_ID, PAYMENTS_THREAD_ID),
        admin_texts,
        messages,
        clock,
        KeyedLocks(),
    )
    return Setup(service, remnawave, payments, messages, alerts, topic, session_factory, clock)


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
    paid_amount: Decimal | None = None,
    status: OrderStatus = OrderStatus.ACCESS_GRANTED,
) -> Order:
    """Оплаченный заказ с выданным доступом: платёж в поддельной платёжке, срок в панели."""
    amount = price if paid_amount is None else paid_amount
    async with setup.session_factory() as session:
        order = Order(client_id=client.id, tariff_id=f"m{days}", price=price, days=days)
        session.add(order)
        await session.flush()
        created = await setup.payments.create_payment(
            PaymentRequest(order.id, price, "Подписка", order.idempotency_key)
        )
        setup.payments.ledger.pay(created.payment_id, paid_at, amount)
        order.payment_provider = setup.payments.name
        order.payment_id = created.payment_id
        order.status = status
        order.paid_at = paid_at
        order.paid_amount = amount
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


async def mark_double(setup: Setup, client: Client, order: Order, first: Order) -> None:
    """Так задача 14 отмечает вероятную двойную оплату: событие оплаты с `possible_double_of`."""
    async with setup.session_factory() as session:
        session.add(
            Event(
                kind=EventKind.PAYMENT_RECEIVED.value,
                client_id=client.id,
                order_id=order.id,
                occurred_at=setup.clock(),
                details={DOUBLE_DETAIL: first.id},
            )
        )
        await session.commit()


async def statuses(setup: Setup) -> list[RefundStatus]:
    async with setup.session_factory() as session:
        return list(await session.scalars(select(RefundRequest.status).order_by(RefundRequest.id)))


async def order_status(setup: Setup, order: Order) -> OrderStatus:
    async with setup.session_factory() as session:
        return (await session.get_one(Order, order.id)).status


async def events(setup: Setup, kind: EventKind) -> list[Event]:
    async with setup.session_factory() as session:
        return list(await session.scalars(select(Event).where(Event.kind == kind.value)))


async def expire_of(setup: Setup) -> datetime:
    return (await setup.remnawave.get_account(TELEGRAM_ID)).expire_at


async def refund(setup: Setup, order: Order) -> RefundResult:
    result = await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id)
    assert isinstance(result, RefundResult)
    return result


# --- Список оплат для «↩️ Вернуть» ---


async def test_list_shows_payments_of_last_30_days_only(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    old = clock() - timedelta(days=30, seconds=1)
    await paid_order(setup, client, paid_at=old, expire_at=old + MONTH)
    recent = await paid_order(
        setup, client, paid_at=clock() - timedelta(days=2), expire_at=old + 2 * MONTH
    )

    payments = await setup.service.refundable_payments(TELEGRAM_ID)

    assert payments == [
        RefundablePayment(
            order_id=recent.id,
            tariff_id="m30",
            days=DAYS,
            paid_at=clock() - timedelta(days=2),
            amount=PRICE,
            duration=MONTH,
            refund_status=None,
            blocker=None,
        )
    ]


async def test_list_marks_refunded_payment(setup: Setup, client: Client, clock: FakeClock) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    await refund(setup, order)

    [payment] = await setup.service.refundable_payments(TELEGRAM_ID) or []

    assert payment.refund_status is RefundStatus.COMPLETED
    assert payment.blocker is RefundRefusal.ALREADY_REFUNDED


async def test_list_of_unknown_client_is_none(setup: Setup) -> None:
    assert await setup.service.refundable_payments(OTHER_ID) is None


# --- Возврат администратором ---


async def test_admin_refund_returns_whole_amount_and_takes_back_its_days(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    # две оплаты подряд: срок до +60 дней; возвращаем вторую — остаётся ровно первая
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    second = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)

    result = await refund(setup, second)

    assert result.progress is RefundProgress.COMPLETED
    assert result.amount == PRICE
    assert result.expire_at == clock() + MONTH
    assert await expire_of(setup) == clock() + MONTH
    [payment] = setup.payments.ledger.find_by_order(second.id)
    assert payment.refunded == PRICE
    assert await order_status(setup, second) is OrderStatus.REFUNDED
    assert await statuses(setup) == [RefundStatus.COMPLETED]
    assert setup.messages.completed == [(TELEGRAM_ID, PRICE, clock() + MONTH)]


async def test_used_days_of_refunded_payment_end_access(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    clock.advance(timedelta(days=10))

    result = await refund(setup, order)

    account = await setup.remnawave.get_account(TELEGRAM_ID)
    assert result.progress is RefundProgress.COMPLETED
    assert result.expire_at is None
    assert account.status is AccountStatus.DISABLED
    assert account.expire_at == clock() + REFUND_END_MARGIN
    assert setup.messages.completed == [(TELEGRAM_ID, PRICE, None)]


async def test_prorated_payment_takes_back_exactly_what_it_gave(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    # оплачено 75 ₽ вместо 150 ₽: оплата дала 15 дней + 2 часа (задача 04 п. 8)
    await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    half = await paid_order(
        setup,
        client,
        paid_at=clock(),
        expire_at=clock() + timedelta(days=45, hours=2),
        paid_amount=Decimal("75"),
    )

    result = await refund(setup, half)

    assert result.amount == Decimal("75")
    assert await expire_of(setup) == clock() + MONTH


async def test_same_payment_cannot_be_refunded_twice(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)
    await refund(setup, order)

    again = await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id)

    assert again == RefundRefused(RefundRefusal.ALREADY_REFUNDED)
    assert await expire_of(setup) == clock() + MONTH
    [payment] = setup.payments.ledger.find_by_order(order.id)
    assert payment.refunded == PRICE


async def test_two_simultaneous_presses_refund_once(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)

    results = await asyncio.gather(
        setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id),
        setup.service.refund_by_admin(AdminRef(901, "Лёша"), TELEGRAM_ID, order.id),
    )

    assert sum(isinstance(r, RefundResult) for r in results) == 1
    assert await statuses(setup) == [RefundStatus.COMPLETED]
    assert await expire_of(setup) == clock() + MONTH


async def test_payment_older_than_30_days_is_not_refunded(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    paid_at = clock() - timedelta(days=30, seconds=1)
    order = await paid_order(setup, client, paid_at=paid_at, expire_at=paid_at + 3 * MONTH)

    result = await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id)

    assert result == RefundRefused(RefundRefusal.WINDOW_PASSED)
    assert setup.payments.ledger.find_by_order(order.id)[0].refunded == 0


async def test_payment_exactly_30_days_old_is_refunded(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    paid_at = clock() - MONTH
    order = await paid_order(setup, client, paid_at=paid_at, expire_at=paid_at + 3 * MONTH)

    result = await refund(setup, order)

    assert result.progress is RefundProgress.COMPLETED
    assert await expire_of(setup) == paid_at + 2 * MONTH


async def test_other_clients_order_is_not_found(
    setup: Setup, client: Client, clock: FakeClock, session: AsyncSession
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    session.add(Client(telegram_id=OTHER_ID, first_seen_at=clock()))
    await session.commit()

    result = await setup.service.refund_by_admin(ADMIN, OTHER_ID, order.id)

    assert result == RefundRefused(RefundRefusal.ORDER_NOT_FOUND)


async def test_payment_without_granted_access_is_not_refunded(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    # доступ ещё выдаётся — возврат сейчас разошёлся бы с повторами выдачи
    order = await paid_order(
        setup, client, paid_at=clock(), expire_at=clock() + MONTH, status=OrderStatus.PAID
    )

    result = await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id)

    assert result == RefundRefused(RefundRefusal.NOT_GRANTED)


async def test_no_refund_through_bot_when_payments_are_off(
    engine: AsyncEngine, clock: FakeClock, mask: Masker, client: Client
) -> None:
    setup = build(create_session_factory(engine), clock, mask, payments_on=False)
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)

    result = await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id)

    assert result == RefundRefused(RefundRefusal.PAYMENTS_OFF)
    assert await order_status(setup, order) is OrderStatus.ACCESS_GRANTED


async def test_refund_after_transfer_is_refused(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    """Дни этой оплаты уже на другом аккаунте: деньги и дни не должны уйти оба."""
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    async with setup.session_factory() as session:
        session.add(
            Event(
                kind=EventKind.ADMIN_ACTION.value,
                client_id=client.id,
                occurred_at=clock() + timedelta(hours=1),
                details={
                    "action": AdminActionKind.SUBSCRIPTION_TRANSFERRED.value,
                    "from_telegram_id": TELEGRAM_ID,
                    "to_telegram_id": OTHER_ID,
                },
            )
        )
        await session.commit()
    clock.advance(timedelta(hours=2))

    result = await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id)

    assert result == RefundRefused(RefundRefusal.TRANSFERRED)


async def test_journal_has_admin_name_and_amounts(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)

    await refund(setup, order)

    [approved] = await events(setup, EventKind.REFUND_APPROVED)
    assert approved.admin_id == ADMIN.telegram_id
    assert approved.details is not None
    assert approved.details["admin_name"] == ADMIN.name
    assert approved.details["kind"] == RefundKind.SUPPORT.value
    [action] = await events(setup, EventKind.ADMIN_ACTION)
    assert action.details is not None
    assert action.details["action"] == AdminActionKind.REFUND.value
    [completed] = await events(setup, EventKind.REFUND_COMPLETED)
    assert completed.details is not None and Decimal(completed.details["amount"]) == PRICE
    assert await events(setup, EventKind.REFUND_TERM_SHORTENED)
    assert "Саша" in setup.topic_texts()[-1]


# --- Платёжка ---


async def test_refund_in_review_waits_and_keeps_days(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)
    setup.payments.review_next_refund()

    result = await refund(setup, order)

    assert result.progress is RefundProgress.IN_REVIEW
    assert await statuses(setup) == [RefundStatus.IN_REVIEW]
    assert await expire_of(setup) == clock() + 2 * MONTH  # дни — когда деньги вернутся
    assert setup.messages.completed == []
    assert await events(setup, EventKind.REFUND_IN_REVIEW)
    assert not await events(setup, EventKind.REFUND_COMPLETED)
    # повторно вернуть нельзя, пока платёжка проверяет
    again = await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id)
    assert again == RefundRefused(RefundRefusal.ALREADY_REFUNDED)


async def test_review_approved_later_finishes_refund(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)
    setup.payments.review_next_refund()
    result = await refund(setup, order)

    progress = await setup.service.settle_review(result.request_id, RefundOutcome.SUCCEEDED)

    assert progress is RefundProgress.COMPLETED
    assert await expire_of(setup) == clock() + MONTH
    assert setup.messages.completed == [(TELEGRAM_ID, PRICE, clock() + MONTH)]
    # повтор уведомления ничего не меняет
    again = await setup.service.settle_review(result.request_id, RefundOutcome.SUCCEEDED)
    assert again is RefundProgress.COMPLETED
    assert await expire_of(setup) == clock() + MONTH


async def test_review_declined_later_makes_payment_refundable_again(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)
    setup.payments.review_next_refund()
    result = await refund(setup, order)

    progress = await setup.service.settle_review(result.request_id, RefundOutcome.CANCELED)

    assert progress is RefundProgress.FAILED
    assert await order_status(setup, order) is OrderStatus.ACCESS_GRANTED
    assert await expire_of(setup) == clock() + 2 * MONTH
    assert setup.alerts.sent


async def test_provider_rejects_refund(setup: Setup, client: Client, clock: FakeClock) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)
    setup.payments.fail_next(PaymentRejected("cancel not supported"))

    result = await refund(setup, order)

    assert result.progress is RefundProgress.FAILED
    assert await statuses(setup) == [RefundStatus.FAILED]
    # деньги не вернулись — оплату можно попробовать вернуть ещё раз
    assert await order_status(setup, order) is OrderStatus.ACCESS_GRANTED
    assert await expire_of(setup) == clock() + 2 * MONTH
    assert "cancel not supported" in setup.alerts.sent[0].text
    assert await events(setup, EventKind.REFUND_FAILED)


async def test_provider_unavailable_is_retried_with_same_key(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)
    setup.payments.fail_next(PaymentUnavailable("timeout"))

    result = await refund(setup, order)
    assert result.progress is RefundProgress.PROVIDER_PENDING
    assert await statuses(setup) == [RefundStatus.APPROVED]

    await setup.service.retry_unfinished()
    await setup.service.retry_unfinished()

    assert await statuses(setup) == [RefundStatus.COMPLETED]
    [payment] = setup.payments.ledger.find_by_order(order.id)
    assert payment.refunded == PRICE
    assert await expire_of(setup) == clock() + MONTH
    assert len(setup.messages.completed) == 1


# --- Панель ---


async def test_panel_down_after_money_returned_is_retried(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)
    setup.remnawave.down = True

    result = await refund(setup, order)

    assert result.progress is RefundProgress.TERM_PENDING
    assert await statuses(setup) == [RefundStatus.REFUNDED]
    assert setup.alerts.sent

    setup.remnawave.down = False
    await setup.service.retry_unfinished()

    assert await statuses(setup) == [RefundStatus.COMPLETED]
    assert await expire_of(setup) == clock() + MONTH


async def test_lost_panel_answer_does_not_shorten_twice(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 3 * MONTH)
    setup.remnawave.expiry_times_out = True

    await refund(setup, order)
    await setup.service.retry_unfinished()

    assert await statuses(setup) == [RefundStatus.COMPLETED]
    assert await expire_of(setup) == clock() + 2 * MONTH


# --- Случайная вторая оплата: клиент сам ---


async def double_paid(setup: Setup, client: Client) -> Order:
    clock = setup.clock
    first = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    second = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + 2 * MONTH)
    await mark_double(setup, client, second, first)
    return second


async def test_client_refunds_second_payment_without_admin(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    second = await double_paid(setup, client)

    offer = await setup.service.double_payment(TELEGRAM_ID, second.id)
    result = await setup.service.refund_double_payment(TELEGRAM_ID, second.id)

    assert isinstance(offer, RefundablePayment) and offer.amount == PRICE
    assert isinstance(result, RefundResult)
    assert result.progress is RefundProgress.COMPLETED
    assert await expire_of(setup) == clock() + MONTH
    [approved] = await events(setup, EventKind.REFUND_APPROVED)
    assert approved.admin_id is None
    assert approved.details is not None
    assert approved.details["kind"] == RefundKind.DOUBLE_PAYMENT.value
    assert not await events(setup, EventKind.ADMIN_ACTION)
    assert any("вторую оплату" in text for text in setup.topic_texts())


async def test_client_cannot_refund_ordinary_payment(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)

    result = await setup.service.refund_double_payment(TELEGRAM_ID, order.id)

    assert result == RefundRefused(RefundRefusal.NOT_DOUBLE_PAYMENT)


async def test_client_cannot_refund_someone_elses_payment(
    setup: Setup, client: Client, clock: FakeClock, session: AsyncSession
) -> None:
    second = await double_paid(setup, client)
    session.add(Client(telegram_id=OTHER_ID, first_seen_at=clock()))
    await session.commit()

    result = await setup.service.refund_double_payment(OTHER_ID, second.id)

    assert result == RefundRefused(RefundRefusal.ORDER_NOT_FOUND)


async def test_client_refund_only_within_seven_days(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    second = await double_paid(setup, client)
    clock.advance(timedelta(days=7, seconds=1))

    result = await setup.service.refund_double_payment(TELEGRAM_ID, second.id)

    assert result == RefundRefused(RefundRefusal.WINDOW_PASSED)
    # через поддержку — можно: правило 30 дней
    assert isinstance(
        await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, second.id), RefundResult
    )


async def test_kept_second_payment_can_be_refunded_only_via_support(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    second = await double_paid(setup, client)

    assert await setup.service.keep_double_payment(TELEGRAM_ID, second.id) is None
    result = await setup.service.refund_double_payment(TELEGRAM_ID, second.id)
    again = await setup.service.keep_double_payment(TELEGRAM_ID, second.id)

    assert result == RefundRefused(RefundRefusal.KEPT)
    assert again == RefundRefused(RefundRefusal.KEPT)
    assert len(await events(setup, EventKind.DOUBLE_PAYMENT_KEPT)) == 1
    assert any("оставил" in text for text in setup.topic_texts())
    assert await expire_of(setup) == clock() + 2 * MONTH


async def test_keep_after_refund_is_refused(setup: Setup, client: Client) -> None:
    second = await double_paid(setup, client)
    await setup.service.refund_double_payment(TELEGRAM_ID, second.id)

    result = await setup.service.keep_double_payment(TELEGRAM_ID, second.id)

    assert result == RefundRefused(RefundRefusal.ALREADY_REFUNDED)


# --- Оплата звёздами Telegram (задача 33): возвращаются звёзды через refundStarPayment ---

STARS = 100
CHARGE = "stxCharge-1"


@dataclass
class FakeStarsRefunds:
    refunded: list[PaidPayment] = field(default_factory=list)

    async def refund(self, payment: PaidPayment, amount: Decimal, idempotency_key: str) -> Refund:
        self.refunded.append(payment)
        return Refund(payment.payment_id, payment.payment_id, amount, RefundOutcome.SUCCEEDED)


async def paid_with_stars(setup: Setup, client: Client, order: Order) -> None:
    """Сделать заказ оплаченным звёздами — как его оставляет `services.stars`."""
    async with setup.session_factory() as session:
        stored = await session.get_one(Order, order.id)
        stored.payment_provider = STARS_PROVIDER
        stored.payment_id = CHARGE
        session.add(
            Event(
                kind=EventKind.PAYMENT_RECEIVED.value,
                client_id=client.id,
                order_id=order.id,
                occurred_at=setup.clock(),
                details={STARS_DETAIL: STARS},
            )
        )
        await session.commit()


async def test_stars_payment_is_refunded_in_stars(
    engine: AsyncEngine, clock: FakeClock, mask: Masker, client: Client
) -> None:
    stars = FakeStarsRefunds()
    setup = build(create_session_factory(engine), clock, mask, stars=stars)
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    await paid_with_stars(setup, client, order)

    [listed] = await setup.service.refundable_payments(TELEGRAM_ID) or []
    result = await refund(setup, order)

    assert (listed.stars, listed.blocker) == (STARS, None)
    assert stars.refunded == [PaidPayment(STARS_PROVIDER, CHARGE, TELEGRAM_ID)]
    assert setup.payments.ledger.find_by_order(order.id)[0].refunded == 0  # СБП не трогали
    assert (result.progress, result.stars) == (RefundProgress.COMPLETED, STARS)
    assert setup.messages.stars == [STARS]
    assert f"{STARS} ⭐" in setup.topic_texts()[0]


async def test_stars_payment_without_stars_route_is_not_refunded(
    setup: Setup, client: Client, clock: FakeClock
) -> None:
    order = await paid_order(setup, client, paid_at=clock(), expire_at=clock() + MONTH)
    await paid_with_stars(setup, client, order)

    result = await setup.service.refund_by_admin(ADMIN, TELEGRAM_ID, order.id)

    assert result == RefundRefused(RefundRefusal.PAYMENTS_OFF)
