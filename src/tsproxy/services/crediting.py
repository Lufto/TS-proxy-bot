"""Зачёт оплаты и выдача доступа (задача 13; задачи 04 и 21 пп. 6–7; аудит §7.1–7.2).

Все пути зачисления — уведомление платёжки, «Проверить оплату», фоновая проверка
(задача 14) — сходятся в одну функцию `CreditingService.credit`:

1. **Зачёт.** Один `UPDATE … WHERE статус «создан» или «отменён»` ставит «оплачено, доступ
   не выдан» и пишет событие «оплата получена» в той же транзакции. Изменилась 0 строк —
   заказ уже засчитан, выходим: двойного зачёта не бывает. Запись не прошла (ошибка базы) —
   повторяем; тревога, только если не вышло ни с одного раза. В той же транзакции снимается
   отметка льготного периода: оплативший не получает «продлите» и не теряет доступ, пока
   доступ выдаётся (задача 11).
2. **Срок «до X».** Под блокировкой клиента бот читает прокси-аккаунт и считает
   `grant_target_expire_at` (`domain.subscription`), записывает его в заказ и только потом
   идёт в Remnawave. Повтор берёт уже записанную дату: «поставь срок до X», а не «добавь
   N дней» — лишних дней не будет, даже если бот упал посреди выдачи.
3. **Выдача.** Создать аккаунт или снять лимиты пробного, поставить срок, включить
   выключенный аккаунт. Статус «доступ выдан», отметка льготного снимается.
4. **Remnawave не ответила** — заказ остаётся «оплачено, доступ не выдан», повторы
   по расписанию `domain.grant` (минута × 30, затем 10 минут до суток, тревога).
   Панель отклонила запрос — повтор не поможет: «на ручной проверке» и тревога.

Срок считается в момент, когда панель впервые ответила после зачёта (аудит §7.2): если
выдача задержалась, клиент не теряет оплаченное время.

Заказы одного клиента выдаются строго по очереди: следующий ждёт, пока выдан предыдущий,
иначе оба посчитали бы новый срок от одного и того же старого.

Задача 14 (решения владельца 2026-10-25):
- **Случайная двойная оплата** — другой заказ клиента засчитан уже после создания этого
  (`domain.payments.possible_double_payment`): дни складываются как обычно, в «💰 Оплаты» —
  пометка, клиенту — кнопки «Вернуть вторую оплату» (полностью, 7 дней) и «Оставить».
  Сам возврат — задача 21 (`services.refunds`); номер первого заказа — в событии оплаты
  (`possible_double_of`), по нему возврат клиентом и разрешается.
- **Доступ не выдан за 30 минут** — вместе с тревогой клиенту второе сообщение
  «оплата получена, доступ задерживается, разбираемся».
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.db.repository import CREDITED_ORDER_STATUSES, claim_order_payment
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import DOUBLE_PAYMENT_REFUND_WINDOW
from tsproxy.domain.dates import format_moscow, require_aware
from tsproxy.domain.grant import plan_grant_retry
from tsproxy.domain.money import format_rubles, paid_duration
from tsproxy.domain.payments import possible_double_payment
from tsproxy.domain.subscription import grant_target_expire_at
from tsproxy.domain.trial import paid_access_limits
from tsproxy.integrations.payments_types import RUB, PaymentState, PaymentStatus
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    ProxyAccount,
    RemnawaveAuthError,
    RemnawaveClient,
    RemnawaveError,
    RemnawaveUnavailable,
)
from tsproxy.services.admin_topics import AdminTopic
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.grace import client_snapshot
from tsproxy.services.locks import KeyedLocks

logger = logging.getLogger(__name__)

# Запись оплаты в базу: столько попыток, пока не тревога (значения выбраны агентом)
CREDIT_WRITE_ATTEMPTS = 3
CREDIT_WRITE_RETRY_DELAY = timedelta(seconds=1)
# Поле события «оплата получена»: отметка льготного периода на момент оплаты
GRACE_DETAIL = "grace_started_at"
# Поле события «оплата получена»: номер заказа, с которым эта оплата похожа на двойную
DOUBLE_DETAIL = "possible_double_of"
# Сколько клиентов фоновая задача повторов берёт за один проход
GRANT_BATCH_SIZE = 50

# Ошибки панели, после которых повтор может помочь: сеть, таймаут, 5xx; неверный токен
# (его заменят); аккаунт пропал между шагами (повтор создаст заново)
RETRYABLE_ERRORS = (RemnawaveUnavailable, RemnawaveAuthError, AccountNotFound)


class OrderNotFound(LookupError):
    """Такого заказа нет."""


class PaymentMismatch(ValueError):
    """Платёж не подтверждает оплату этого заказа: не тот заказ или платёж, не оплачен,
    не рубли, сумма не больше нуля. Засчитывать нельзя."""


class PaymentOutcome(StrEnum):
    """Чем кончился зачёт оплаты для заказа."""

    GRANTED = "granted"  # оплата засчитана, доступ выдан
    PENDING = "pending"  # засчитана, доступ не выдан — повторы идут сами
    MANUAL_REVIEW = "manual_review"  # засчитана, выдать автоматически нельзя — тревога
    ACCESS_FAILED = "access_failed"  # засчитана, повторы кончились за сутки — тревога
    ALREADY_CREDITED = "already_credited"  # заказ засчитан раньше, ничего не сделано


ORDER_OUTCOMES = {
    OrderStatus.ACCESS_GRANTED: PaymentOutcome.GRANTED,
    OrderStatus.PAID: PaymentOutcome.PENDING,
    OrderStatus.MANUAL_REVIEW: PaymentOutcome.MANUAL_REVIEW,
    OrderStatus.ACCESS_FAILED: PaymentOutcome.ACCESS_FAILED,
}


@dataclass(frozen=True)
class CreditResult:
    order_id: int
    outcome: PaymentOutcome


@dataclass(frozen=True)
class GrantedAccess:
    """Доступ по оплате выдан — клиенту нужно сообщение «✅ Оплачено, подписка до …»."""

    telegram_id: int
    order_id: int
    account: ProxyAccount
    price: Decimal  # цена тарифа
    paid_amount: Decimal  # сколько заплачено на самом деле

    @property
    def prorated(self) -> bool:
        """Оплачено не на сумму тарифа: срок пересчитан, клиенту — извинение."""
        return self.paid_amount != self.price


class PaymentMessages(Protocol):
    """Сообщения клиенту об оплате. Настоящие — `bot.payments`, в тестах — подделка.

    Реализация сама разбирается с ошибками Telegram: оплата к этому моменту уже засчитана.
    """

    async def access_granted(self, granted: GrantedAccess) -> None: ...

    async def access_pending(self, telegram_id: int) -> None:
        """«Оплата получена, доступ подключается» — когда сразу выдать не вышло."""
        ...

    async def access_delayed(self, telegram_id: int) -> None:
        """«Доступ задерживается, разбираемся» — доступ не выдан за 30 минут (задача 14)."""
        ...

    async def possible_double_payment(
        self, telegram_id: int, order_id: int, refund_until: datetime
    ) -> None:
        """«Получили две оплаты, дни сложены» и кнопки «Вернуть вторую оплату» (до
        `refund_until`) и «Оставить» для заказа `order_id` (задачи 14, 21)."""
        ...


@dataclass(frozen=True)
class _CreditedPayment:
    """Засчитанная оплата — для сообщения в «💰 Оплаты»."""

    order_id: int
    client_id: int
    telegram_id: int
    name: str | None
    username: str | None
    tariff_id: str
    days: int
    price: Decimal
    paid_amount: Decimal
    paid_at: datetime
    double_of: int | None  # заказ, с которым эта оплата похожа на случайную двойную


class CreditingService:
    """Засчитать оплату ровно один раз и выдать доступ в Remnawave."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        events: EventLog,
        alerter: Alerter,
        payments_topic: AdminTopic,
        admin_texts: Texts,
        messages: PaymentMessages,
        clock: Clock,
        locks: KeyedLocks,
        *,
        write_retry_delay: timedelta = CREDIT_WRITE_RETRY_DELAY,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._events = events
        self._alerter = alerter
        self._payments_topic = payments_topic
        self._admin_texts = admin_texts
        self._messages = messages
        self._clock = clock
        self._locks = locks
        self._write_retry_delay = write_retry_delay

    # --- Зачёт ---

    async def credit(self, order_id: int, payment: PaymentState) -> CreditResult:
        """Засчитать оплату заказа и сразу попробовать выдать доступ.

        `payment` — состояние платежа, полученное **у платёжки** (`get_payment`), а не из
        уведомления. Не тот заказ или платёж — `PaymentMismatch`, нет заказа — `OrderNotFound`.
        Повторный вызов безопасен: `ALREADY_CREDITED`. Ошибку базы, не прошедшую и после
        повторов, бросает дальше (после тревоги) — вызывающий ответит платёжке ошибкой,
        и та пришлёт уведомление ещё раз.
        """
        credited = await self._record_payment(order_id, payment)
        if credited is None:
            logger.info("Заказ %s уже засчитан — повтор игнорируем", order_id)
            return CreditResult(order_id, PaymentOutcome.ALREADY_CREDITED)
        logger.info("Заказ %s: оплата %s засчитана", order_id, payment.payment_id)
        await self._announce(credited)
        await self._grant_client(credited.client_id)
        outcome = await self._outcome(order_id)
        if outcome is not PaymentOutcome.GRANTED:
            await self._messages.access_pending(credited.telegram_id)
        if credited.double_of is not None:
            await self._messages.possible_double_payment(
                credited.telegram_id,
                credited.order_id,
                credited.paid_at + DOUBLE_PAYMENT_REFUND_WINDOW,
            )
        return CreditResult(order_id, outcome)

    async def _record_payment(
        self, order_id: int, payment: PaymentState
    ) -> _CreditedPayment | None:
        """Записать зачёт, повторяя при ошибке базы. `None` — заказ уже засчитан."""
        attempt = 1
        while True:
            try:
                return await self._try_record(order_id, payment)
            except SQLAlchemyError as error:
                if attempt >= CREDIT_WRITE_ATTEMPTS:
                    logger.exception("Заказ %s: оплата не записалась в базу", order_id)
                    await self._alerter.raise_alert(
                        f"payment_not_recorded:{order_id}",
                        self._admin_texts.get(
                            "alerts.payment_not_recorded",
                            order_id=order_id,
                            payment_id=payment.payment_id,
                            error=type(error).__name__,
                        ),
                    )
                    raise
                logger.warning(
                    "Заказ %s: оплата не записалась (попытка %d): %s", order_id, attempt, error
                )
            attempt += 1
            await asyncio.sleep(self._write_retry_delay.total_seconds())

    async def _try_record(self, order_id: int, payment: PaymentState) -> _CreditedPayment | None:
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                raise OrderNotFound(f"заказа {order_id} нет")
            _check_payment(order, payment)
            claimed = await claim_order_payment(
                session, order.id, payment.payment_id, payment.amount, now
            )
            if not claimed:
                return None
            client = await session.get_one(Client, order.client_id)
            double_of = await _double_payment_of(session, order)
            grace_started_at = client.grace_started_at
            # Оплатил — льготный период кончился сразу: без напоминаний «продлите» и без
            # выключения доступа, пока выдаётся доступ. Отметку храним в событии: по ней
            # новый срок считается от момента оплаты, а не от конца льготного (задача 04 п. 2)
            client.grace_started_at = None
            await self._events.record(
                session,
                EventKind.PAYMENT_RECEIVED,
                client_id=order.client_id,
                order_id=order.id,
                details={
                    "provider": order.payment_provider,
                    "payment_id": payment.payment_id,
                    "amount": str(payment.amount),
                    "price": str(order.price),
                    "provider_paid_at": (
                        None if payment.paid_at is None else payment.paid_at.isoformat()
                    ),
                    "test": payment.test,
                    GRACE_DETAIL: (
                        None if grace_started_at is None else grace_started_at.isoformat()
                    ),
                    DOUBLE_DETAIL: double_of,
                },
            )
            credited = _CreditedPayment(
                order_id=order.id,
                client_id=client.id,
                telegram_id=client.telegram_id,
                name=client.name,
                username=client.username,
                tariff_id=order.tariff_id,
                days=order.days,
                price=order.price,
                paid_amount=payment.amount,
                paid_at=now,
                double_of=double_of,
            )
            await session.commit()
        return credited

    async def _announce(self, credited: _CreditedPayment) -> None:
        """Сообщение в «💰 Оплаты»; сумма не совпала с тарифом — ещё и тревога.

        Подстановки (имя клиента и т. п.) экранирует для HTML `Texts.get`.
        """
        prorated = credited.paid_amount != credited.price
        username = credited.username or self._admin_texts.get("payments.no_username")
        text = self._admin_texts.get(
            "payments.received_prorated" if prorated else "payments.received",
            order_id=credited.order_id,
            amount=format_rubles(credited.paid_amount),
            price=format_rubles(credited.price),
            tariff_id=credited.tariff_id,
            days=credited.days,
            name=credited.name or "",
            username=username,
            telegram_id=credited.telegram_id,
        )
        if credited.double_of is not None:
            text += "\n" + self._admin_texts.get(
                "payments.possible_double",
                other_order_id=credited.double_of,
                refund_until=format_moscow(credited.paid_at + DOUBLE_PAYMENT_REFUND_WINDOW),
            )
        await self._payments_topic.post(text)
        if prorated:
            logger.warning(
                "Заказ %s: оплачено %s вместо %s",
                credited.order_id,
                credited.paid_amount,
                credited.price,
            )
            await self._alerter.raise_alert(
                f"payment_amount_mismatch:{credited.order_id}",
                self._admin_texts.get(
                    "alerts.payment_amount_mismatch",
                    order_id=credited.order_id,
                    amount=format_rubles(credited.paid_amount),
                    price=format_rubles(credited.price),
                ),
            )

    async def _outcome(self, order_id: int) -> PaymentOutcome:
        async with self._session_factory() as session:
            status = await session.scalar(select(Order.status).where(Order.id == order_id))
        if status not in ORDER_OUTCOMES:
            raise ValueError(f"заказ {order_id} после зачёта в статусе {status}")
        return ORDER_OUTCOMES[status]

    # --- Выдача доступа ---

    async def retry_grants(self) -> None:
        """Фоновая задача (раз в минуту): повторить выдачу, у которой подошло время."""
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            client_ids = list(
                await session.scalars(
                    select(Order.client_id)
                    .where(
                        Order.status == OrderStatus.PAID,
                        or_(
                            Order.next_grant_attempt_at.is_(None),
                            Order.next_grant_attempt_at <= now,
                        ),
                    )
                    .distinct()
                    .order_by(Order.client_id)
                    .limit(GRANT_BATCH_SIZE)
                )
            )
        for client_id in client_ids:
            await self._grant_client(client_id)

    async def _grant_client(self, client_id: int) -> None:
        """Выдать доступ по всем оплаченным заказам клиента — по очереди, под его блокировкой."""
        granted: list[GrantedAccess] = []
        async with self._locks.hold(client_id):
            async with self._session_factory() as session:
                telegram_id = await session.scalar(
                    select(Client.telegram_id).where(Client.id == client_id)
                )
                orders = list(
                    await session.scalars(
                        select(Order)
                        .where(Order.client_id == client_id, Order.status == OrderStatus.PAID)
                        .order_by(Order.id)
                    )
                )
            if telegram_id is None:
                raise LookupError(f"клиента {client_id} нет")
            for order in orders:
                now = utc_now(self._clock)
                due = order.next_grant_attempt_at
                if due is not None and due > now:
                    break  # ранний заказ ждёт своего повтора — поздние ждут вместе с ним
                outcome, access = await self._grant_order(telegram_id, order, now)
                if access is not None:
                    granted.append(access)
                if outcome is PaymentOutcome.PENDING:
                    break
        for access in granted:
            await self._messages.access_granted(access)

    async def _grant_order(
        self, telegram_id: int, order: Order, now: datetime
    ) -> tuple[PaymentOutcome, GrantedAccess | None]:
        try:
            target = order.grant_target_expire_at
            if target is None:
                target = await self._fix_target(telegram_id, order, now)
            account = await self._apply(telegram_id, target)
        except RETRYABLE_ERRORS as error:
            return await self._schedule_retry(telegram_id, order, now, error), None
        except RemnawaveError as error:
            return await self._send_to_manual_review(telegram_id, order, error), None
        access = await self._mark_granted(telegram_id, order, account)
        return PaymentOutcome.GRANTED, access

    async def _fix_target(self, telegram_id: int, order: Order, now: datetime) -> datetime:
        """Посчитать «продлить до X» по текущему состоянию клиента и записать в заказ."""
        if order.paid_amount is None:
            raise ValueError(f"заказ {order.id} оплачен без суммы")
        async with self._session_factory() as session:
            client = await session.get_one(Client, order.client_id)
            grace_started_at = client.grace_started_at or await _grace_at_payment(session, order.id)
        account = await self._find_account(telegram_id)
        if account is None:
            snapshot = ClientSnapshot(None, False, grace_started_at, access_disabled=False)
        else:
            snapshot = replace(client_snapshot(client, account), grace_started_at=grace_started_at)
        target = grant_target_expire_at(
            snapshot, now, paid_duration(order.days, order.price, order.paid_amount)
        )
        async with self._session_factory() as session:
            await session.execute(
                update(Order)
                .where(Order.id == order.id, Order.grant_target_expire_at.is_(None))
                .values(grant_target_expire_at=target)
            )
            await session.commit()
        order.grant_target_expire_at = target
        logger.info("Заказ %s: продлить до %s", order.id, target.isoformat())
        return target

    async def _find_account(self, telegram_id: int) -> ProxyAccount | None:
        try:
            return await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            return None  # нормальная ситуация: клиент покупает, не взяв пробный

    async def _apply(self, telegram_id: int, target: datetime) -> ProxyAccount:
        """Привести аккаунт к оплаченному: лимиты тарифа, срок не раньше `target`, включён.

        Каждый шаг безопасно повторять: значения абсолютные, сделанное не делается заново.
        """
        limits = paid_access_limits()
        account = await self._remnawave.get_or_create_account(
            telegram_id,
            expire_at=target,
            device_limit=limits.device_limit,
            traffic_limit_bytes=limits.traffic_limit_bytes,
        )
        current = (account.traffic_limit_bytes, account.device_limit)
        if current != (limits.traffic_limit_bytes, limits.device_limit):
            # покупка во время пробного: снять 7 ГБ, устройства как у тарифа (аудит §7.6)
            account = await self._remnawave.set_limits(
                telegram_id,
                device_limit=limits.device_limit,
                traffic_limit_bytes=limits.traffic_limit_bytes,
            )
        if account.expire_at < target:
            account = await self._remnawave.set_expiry(telegram_id, target)
        if account.status is AccountStatus.DISABLED:
            # льготный период кончился и бот выключил доступ (задача 11)
            account = await self._remnawave.enable_account(telegram_id)
        return account

    async def _mark_granted(
        self, telegram_id: int, order: Order, account: ProxyAccount
    ) -> GrantedAccess:
        if order.paid_amount is None:
            raise ValueError(f"заказ {order.id} оплачен без суммы")
        async with self._session_factory() as session:
            await session.execute(
                update(Order)
                .where(Order.id == order.id, Order.status == OrderStatus.PAID)
                .values(status=OrderStatus.ACCESS_GRANTED, next_grant_attempt_at=None)
            )
            # Отметку сняли при зачёте; если льготный успел начаться снова, пока доступ
            # выдавался (срок истёк во время повторов), — новый срок уже пошёл, снять ещё раз
            await session.execute(
                update(Client).where(Client.id == order.client_id).values(grace_started_at=None)
            )
            await self._events.record(
                session,
                EventKind.ACCESS_GRANTED,
                client_id=order.client_id,
                order_id=order.id,
                details={
                    "expire_at": account.expire_at.isoformat(),
                    "grant_attempts": order.grant_attempts + 1,
                },
            )
            await session.commit()
        logger.info("Заказ %s: доступ выдан до %s", order.id, account.expire_at.isoformat())
        return GrantedAccess(telegram_id, order.id, account, order.price, order.paid_amount)

    async def _schedule_retry(
        self, telegram_id: int, order: Order, now: datetime, error: RemnawaveError
    ) -> PaymentOutcome:
        """Панель не ответила: следующий повтор по расписанию или конец повторов."""
        if order.paid_at is None:
            raise ValueError(f"заказ {order.id} в статусе «оплачен» без времени оплаты")
        failed = order.grant_attempts + 1
        plan = plan_grant_retry(order.paid_at, failed, now)
        gave_up = plan.next_attempt_at is None
        logger.warning("Заказ %s: доступ не выдан (попытка %d): %s", order.id, failed, error)
        async with self._session_factory() as session:
            await session.execute(
                update(Order)
                .where(Order.id == order.id, Order.status == OrderStatus.PAID)
                .values(
                    grant_attempts=failed,
                    next_grant_attempt_at=plan.next_attempt_at,
                    status=OrderStatus.ACCESS_FAILED if gave_up else OrderStatus.PAID,
                )
            )
            if gave_up:
                await self._record_failure(session, order, error, "retries_exhausted")
            await session.commit()
        if plan.escalate:
            await self._alert_order("grant_delayed", telegram_id, order, error)
            await self._messages.access_delayed(telegram_id)
        if gave_up:
            await self._alert_order("grant_failed", telegram_id, order, error)
            return PaymentOutcome.ACCESS_FAILED
        return PaymentOutcome.PENDING

    async def _send_to_manual_review(
        self, telegram_id: int, order: Order, error: RemnawaveError
    ) -> PaymentOutcome:
        """Панель отклонила запрос — повтор не поможет, выдаст администратор."""
        logger.error("Заказ %s: выдать доступ нельзя: %s", order.id, error)
        async with self._session_factory() as session:
            await session.execute(
                update(Order)
                .where(Order.id == order.id, Order.status == OrderStatus.PAID)
                .values(
                    status=OrderStatus.MANUAL_REVIEW,
                    grant_attempts=order.grant_attempts + 1,
                    next_grant_attempt_at=None,
                )
            )
            await self._record_failure(session, order, error, "manual_review")
            await session.commit()
        await self._alert_order("grant_manual", telegram_id, order, error)
        return PaymentOutcome.MANUAL_REVIEW

    async def _record_failure(
        self, session: AsyncSession, order: Order, error: RemnawaveError, reason: str
    ) -> None:
        await self._events.record(
            session,
            EventKind.ACCESS_FAILED,
            client_id=order.client_id,
            order_id=order.id,
            details={"reason": reason, "error": type(error).__name__},
        )

    async def _alert_order(
        self, kind: str, telegram_id: int, order: Order, error: RemnawaveError
    ) -> None:
        await self._alerter.raise_alert(
            f"{kind}:{order.id}",
            self._admin_texts.get(
                f"alerts.{kind}",
                order_id=order.id,
                telegram_id=telegram_id,
                error=f"{type(error).__name__}: {error}",
            ),
        )


def _check_payment(order: Order, payment: PaymentState) -> None:
    """Платёж подтверждает оплату именно этого заказа — иначе засчитывать нельзя."""
    problems = []
    if payment.status is not PaymentStatus.SUCCEEDED:
        problems.append(f"статус {payment.status.value}")
    if order.payment_id is None or payment.payment_id != order.payment_id:
        problems.append(f"платёж {payment.payment_id} не этого заказа")
    if payment.order_id != order.id:
        problems.append(f"платёж для заказа {payment.order_id}")
    if payment.currency != RUB:
        problems.append(f"валюта {payment.currency}")
    if payment.amount <= 0:
        problems.append(f"сумма {payment.amount}")
    if problems:
        raise PaymentMismatch(f"заказ {order.id}: " + ", ".join(problems))


async def _double_payment_of(session: AsyncSession, order: Order) -> int | None:
    """Другой засчитанный заказ клиента, с которым эта оплата похожа на случайную двойную."""
    other = (
        await session.execute(
            select(Order.id, Order.paid_at)
            .where(
                Order.client_id == order.client_id,
                Order.id != order.id,
                Order.status.in_(CREDITED_ORDER_STATUSES),
            )
            .order_by(Order.paid_at.desc(), Order.id.desc())
            .limit(1)
        )
    ).first()
    if other is None or not possible_double_payment(order.created_at, other.paid_at):
        return None
    return other.id


async def _grace_at_payment(session: AsyncSession, order_id: int) -> datetime | None:
    """Отметка «льготный начался», снятая при зачёте этого заказа (из события оплаты)."""
    details = await session.scalar(
        select(Event.details).where(
            Event.order_id == order_id, Event.kind == EventKind.PAYMENT_RECEIVED.value
        )
    )
    raw = None if details is None else details.get(GRACE_DETAIL)
    return None if raw is None else require_aware(datetime.fromisoformat(raw))
