"""Общее для тестов оплаты (задачи 13–14): подделки сообщений, платёжки и базы, сборка сервисов."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.content.loader import ADMIN_TEXTS_FILE, Texts, load_texts
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.payments_types import PaymentRequest, PaymentState
from tsproxy.services.admin_topics import AdminTopic
from tsproxy.services.alerts import Alerter
from tsproxy.services.crediting import CreditingService, GrantedAccess
from tsproxy.services.events import EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.payment_checks import PaymentChecks

ADMIN_TEXTS = Path(__file__).parent.parent.parent / "content" / ADMIN_TEXTS_FILE
ADMIN_CHAT_ID = -100500
ALERTS_THREAD_ID = 7
PAYMENTS_THREAD_ID = 8
PRICE = Decimal("150")
DAYS = 30
CREDIT_NO_DELAY = timedelta(0)  # повтор записи оплаты в тестах — без паузы


@dataclass
class FakePaymentMessages:
    """Сообщения клиенту об оплате — запоминаем вместо Telegram."""

    granted: list[GrantedAccess] = field(default_factory=list)
    pending: list[int] = field(default_factory=list)
    delayed: list[int] = field(default_factory=list)
    doubles: list[tuple[int, datetime]] = field(default_factory=list)

    async def access_granted(self, granted: GrantedAccess) -> None:
        self.granted.append(granted)

    async def access_pending(self, telegram_id: int) -> None:
        self.pending.append(telegram_id)

    async def access_delayed(self, telegram_id: int) -> None:
        self.delayed.append(telegram_id)

    async def possible_double_payment(self, telegram_id: int, refund_until: datetime) -> None:
        self.doubles.append((telegram_id, refund_until))


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


class CountingPayments(FakePaymentProvider):
    """Поддельная платёжка, которая считает вопросы «что с платежом» и может «соврать»
    о признаке «тестовый» (`test_flag`)."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock)
        self.asked: list[str] = []
        self.test_flag: bool | None = None

    async def get_payment(self, payment_id: str) -> PaymentState:
        self.asked.append(payment_id)
        state = await super().get_payment(payment_id)
        return state if self.test_flag is None else replace(state, test=self.test_flag)


@dataclass
class PaymentSetup:
    """Зачёт и проверки оплат на подделках: платёжка, панель, Telegram."""

    checks: PaymentChecks
    crediting: CreditingService
    payments: CountingPayments
    remnawave: FakeRemnawave
    messages: FakePaymentMessages
    alerts: FakeAlertSender
    topic: FakeAlertSender
    admin_texts: Texts
    session_factory: async_sessionmaker[AsyncSession]
    clock: FakeClock

    async def order(
        self,
        client: Client,
        *,
        paid: bool = False,
        status: OrderStatus = OrderStatus.CREATED,
        price: Decimal = PRICE,
    ) -> int:
        """Заказ месяца с платежом у платёжки (`paid` — клиент уже оплатил)."""
        async with self.session_factory() as session:
            order = Order(
                client_id=client.id,
                tariff_id="m1",
                price=price,
                days=DAYS,
                status=status,
                created_at=self.clock(),
            )
            session.add(order)
            await session.flush()
            created = await self.payments.create_payment(
                PaymentRequest(order.id, price, "Подписка", order.idempotency_key)
            )
            order.payment_provider = self.payments.name
            order.payment_id = created.payment_id
            order.payment_url = created.url
            order.payment_url_expires_at = created.expires_at
            await session.commit()
            order_id = order.id
        if paid:
            self.payments.ledger.pay(created.payment_id, self.clock())
        return order_id

    async def load(self, order_id: int) -> Order:
        async with self.session_factory() as session:
            return await session.get_one(Order, order_id)

    async def payment_id(self, order_id: int) -> str:
        payment_id = (await self.load(order_id)).payment_id
        assert payment_id is not None
        return payment_id

    async def set_status(self, order_id: int, status: OrderStatus) -> None:
        async with self.session_factory() as session:
            (await session.get_one(Order, order_id)).status = status
            await session.commit()

    async def events(self, kind: str) -> list[Event]:
        async with self.session_factory() as session:
            return list(await session.scalars(select(Event).where(Event.kind == kind)))


def build_payment_setup(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    *,
    test_payments: bool = True,
    crediting_sessions: async_sessionmaker[AsyncSession] | None = None,
    payments_off: bool = False,
) -> PaymentSetup:
    """`crediting_sessions` — своя фабрика сессий для зачёта (например, с падающей базой).

    `payments_off` — оплата выключена: проверки получают `None` вместо платёжки.
    """
    payments = CountingPayments(clock)
    remnawave = FakeRemnawave(RunMode.PROD, now=clock)
    messages = FakePaymentMessages()
    alerts = FakeAlertSender()
    topic = FakeAlertSender()
    admin_texts = load_texts(ADMIN_TEXTS)
    events = EventLog(clock, mask)
    alerter = Alerter(alerts, admin_texts, ADMIN_CHAT_ID, ALERTS_THREAD_ID, clock, mask)
    crediting = CreditingService(
        crediting_sessions or session_factory,
        remnawave,
        events,
        alerter,
        AdminTopic(topic, ADMIN_CHAT_ID, PAYMENTS_THREAD_ID),
        admin_texts,
        messages,
        clock,
        KeyedLocks(),
        write_retry_delay=CREDIT_NO_DELAY,
    )
    checks = PaymentChecks(
        session_factory,
        None if payments_off else payments,
        crediting,
        events,
        alerter,
        admin_texts,
        clock,
        test_payments=test_payments,
    )
    return PaymentSetup(
        checks,
        crediting,
        payments,
        remnawave,
        messages,
        alerts,
        topic,
        admin_texts,
        session_factory,
        clock,
    )
