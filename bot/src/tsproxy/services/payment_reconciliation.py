"""Ежедневная сверка оплат с платёжкой (задача 14; решение владельца 2026-10-25, п. 4;
карта оплаты, уязвимости 2, 6, 8).

Раз в сутки (в 09:00 МСК) бот берёт у платёжки список платежей за прошедшие сутки
и сравнивает с заказами (`domain.payments.reconcile_payments`).
Расхождения — одна тревога со списком:

- деньги у платёжки, а заказа нет (второе списание, сбой записи платежа);
- оплачено, а заказ не засчитан — его сверка сразу засчитывает обычным путём
  (перепроверка у платёжки, `PaymentChecks.settle`), но администраторы всё равно узнают:
  значит, не сработали ни уведомление, ни кнопка, ни фоновая проверка;
- засчитано у нас, а у платёжки не оплачено (или платежа нет в списке, и перепроверка
  по одному это подтвердила);
- возврат у платёжки, которого нет в боте: возвраты делаются только через бота.

Самые свежие платежи (последние полчаса) в сверку не входят: они ещё могут быть в пути.
Оплата выключена — сверять не с кем.

Задача 15 (Platega): список — выгрузка `POST /transaction/export/json`. Платёж, деньги
по которому потом вернули (спор через банк, возврат), считается оплаченным с возвратом:
если возврат сделан не через бота — строка «возврат мимо бота». Платёжка отказала или
ответила не по документации — тревога с её ответом.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Order
from tsproxy.db.repository import CREDITED_ORDER_STATUSES
from tsproxy.domain.constants import PAYMENTS_RECONCILE_MARGIN, PAYMENTS_RECONCILE_WINDOW
from tsproxy.domain.payments import (
    OrderRecord,
    ProviderRecord,
    ReconcileIssue,
    ReconcileMismatch,
    reconcile_payments,
)
from tsproxy.integrations.payments_types import (
    PAID_STATUSES,
    PaymentError,
    PaymentNotFound,
    PaymentProvider,
    PaymentState,
    PaymentStatus,
    PaymentUnavailable,
)
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.payment_checks import CheckOutcome, PaymentChecks

logger = logging.getLogger(__name__)

RECONCILE_ALERT = "payments_reconcile"
RECONCILE_UNAVAILABLE_ALERT = "payments_reconcile_unavailable"
RECONCILE_FAILED_ALERT = "payments_reconcile_failed"
# Возврат у платёжки: целиком (`REFUNDED`) или частично; сумму частичного Platega не сообщает
REFUNDED_STATUSES = (PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED)
# Столько расхождений перечисляем в тревоге, остальные — в логе
MAX_LISTED_ISSUES = 20
# Чем кончилась попытка засчитать найденную сверкой оплату
SETTLED_OUTCOMES = (CheckOutcome.CREDITED, CheckOutcome.ALREADY_CREDITED)


class PaymentReconciliation:
    """Сверить платежи платёжки за сутки с заказами и поднять тревогу о расхождениях."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        payments: PaymentProvider | None,  # None — оплата выключена (задача 29)
        checks: PaymentChecks,
        alerter: Alerter,
        admin_texts: Texts,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._payments = payments
        self._checks = checks
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._clock = clock

    async def run(self) -> None:
        """Фоновая задача раз в сутки, в 09:00 МСК."""
        payments = self._payments
        if payments is None:
            return  # оплата выключена — сверять не с кем
        until = utc_now(self._clock) - PAYMENTS_RECONCILE_MARGIN
        since = until - PAYMENTS_RECONCILE_WINDOW
        try:
            listed = await payments.list_payments(since, until)
        except PaymentUnavailable as error:
            logger.warning("Сверка оплат: платёжка не ответила: %s", error)
            await self._alerter.raise_alert(
                RECONCILE_UNAVAILABLE_ALERT,
                self._admin_texts.get("alerts.payments_reconcile_unavailable"),
            )
            return
        except PaymentError as error:
            logger.error("Сверка оплат: платёжка отказала: %s", error)
            await self._alerter.raise_alert(
                RECONCILE_FAILED_ALERT,
                self._admin_texts.get("alerts.payments_reconcile_failed", error=str(error)),
            )
            return
        orders = await self._orders(payments.name, [p.payment_id for p in listed], since, until)
        issues = reconcile_payments([_provider_record(p) for p in listed], list(orders.values()))
        lines = [line for issue in issues if (line := await self._describe(issue)) is not None]
        if not lines:
            logger.info("Сверка оплат: %d платежей, расхождений нет", len(listed))
            return
        logger.warning("Сверка оплат: расхождений %d: %s", len(lines), "; ".join(lines))
        await self._alerter.raise_alert(
            RECONCILE_ALERT,
            "\n".join(
                [
                    self._admin_texts.get("alerts.payments_reconcile", count=len(lines)),
                    *lines[:MAX_LISTED_ISSUES],
                ]
            ),
        )

    async def _orders(
        self, provider: str, payment_ids: Sequence[str], since: datetime, until: datetime
    ) -> dict[str, OrderRecord]:
        """Заказы с платежами из списка и засчитанные заказы, созданные за то же время."""
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(Order).where(
                    Order.payment_provider == provider,
                    Order.payment_id.is_not(None),
                    or_(
                        Order.payment_id.in_(payment_ids),
                        (Order.created_at >= since)
                        & (Order.created_at < until)
                        & Order.status.in_(CREDITED_ORDER_STATUSES),
                    ),
                )
            )
            return {
                order.payment_id: OrderRecord(
                    order.id,
                    order.payment_id,
                    credited=order.status in CREDITED_ORDER_STATUSES,
                    refunded=order.status is OrderStatus.REFUNDED,
                )
                for order in rows
                if order.payment_id is not None
            }

    async def _describe(self, issue: ReconcileIssue) -> str | None:
        """Строка тревоги о расхождении; `None` — перепроверка показала, что всё в порядке."""
        values = {"order_id": issue.order_id, "payment_id": issue.payment_id}
        if issue.kind is ReconcileMismatch.PAID_NOT_CREDITED:
            outcome = await self._settle(issue)
            key = "credited_now" if outcome in SETTLED_OUTCOMES else "not_credited"
            return self._admin_texts.get(f"payments.reconcile.{key}", **values)
        if issue.kind is ReconcileMismatch.MISSING_AT_PROVIDER and await self._paid(issue):
            return None  # список не захватил платёж по времени, а сам платёж оплачен
        return self._admin_texts.get(f"payments.reconcile.{issue.kind.value}", **values)

    async def _settle(self, issue: ReconcileIssue) -> CheckOutcome | None:
        """Засчитать найденную сверкой оплату обычным путём. `None` — платёжка не ответила."""
        async with self._session_factory() as session:
            order = await session.get_one(Order, issue.order_id)
        try:
            return await self._checks.settle(order)
        except PaymentUnavailable as error:
            logger.warning("Сверка оплат: заказ %s не засчитан: %s", issue.order_id, error)
            return None

    async def _paid(self, issue: ReconcileIssue) -> bool:
        """Перепроверить платёж, которого не оказалось в списке платёжки."""
        if self._payments is None:
            raise ValueError("сверка без платёжки")
        try:
            state = await self._payments.get_payment(issue.payment_id)
        except PaymentNotFound:
            return False
        except PaymentUnavailable as error:
            # не удалось перепроверить — лучше лишняя строка в тревоге, чем пропуск
            logger.warning("Сверка оплат: платёж %s не перепроверен: %s", issue.payment_id, error)
            return False
        return state.status is PaymentStatus.SUCCEEDED


def _provider_record(payment: PaymentState) -> ProviderRecord:
    return ProviderRecord(
        payment.payment_id,
        paid=payment.status in PAID_STATUSES,
        refunded=payment.refunded > 0 or payment.status in REFUNDED_STATUSES,
    )
