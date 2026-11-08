"""Оплата звёздами Telegram (задача 33; решения владельца 2026-10-30).

Путь оплаты:

1. **«⭐ Stars»** на экране тарифа — новый заказ звёздами (`OrderService.stars_order`) и счёт
   (`sendInvoice` делает бот) с ценой `domain.stars.stars_price` и `payload` = номер заказа.
2. **`pre_checkout_query`** — Telegram спрашивает «можно оплатить?» и ждёт ответа 10 секунд.
   Здесь только чтение заказа из базы, без запросов наружу: заказ этого клиента, оплата
   звёздами, «создан», счёт не старше часа, сумма в звёздах совпадает (`pre_checkout_refusal`).
3. **`successful_payment`** — номер платежа Telegram (`telegram_payment_charge_id`) пишется
   в заказ одним `UPDATE … WHERE номера ещё нет` и дальше — тот же путь, что у СБП:
   `CreditingService.credit` засчитывает ровно один раз, выдаёт доступ и шлёт «✅ Оплачено».
   Пара «платёжка + номер платежа» в заказах уникальна: один платёж — один заказ.

Оплата звёздами переводится в рубли заказа (`domain.stars.paid_rubles`): для зачёта это
обычная оплата в рублях с пометкой «звёздами, N ⭐» (`PaymentState.stars`) — срок, пропорции
и возвраты считаются как у СБП.

**Оплата не подошла к заказу** (чужой или неизвестный счёт, заказ уже оплачен другим
платежом, засчитать нельзя) — звёзды сразу возвращаются клиенту (`refundStarPayment`),
администраторам — тревога. Деньги, которые бот не засчитал, у нас не остаются (решение агента).
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Tariff, Texts
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Order
from tsproxy.db.repository import CREDITED_ORDER_STATUSES, affected_rows
from tsproxy.domain.stars import (
    STARS_CURRENCY,
    StarsOrder,
    StarsRefusal,
    paid_rubles,
    pre_checkout_refusal,
    stars_in_rubles,
    stars_price,
)
from tsproxy.integrations.payments_types import (
    RUB,
    PaidPayment,
    PaymentError,
    PaymentRefunds,
    PaymentState,
    PaymentStatus,
)
from tsproxy.integrations.telegram_stars import STARS_PROVIDER
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.crediting import CreditingService, PaymentMismatch, PaymentOutcome
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.orders import OrderService

logger = logging.getLogger(__name__)

# Заказ с засчитанной оплатой — платить по счёту ещё раз нельзя
PAID_STATUSES = (*CREDITED_ORDER_STATUSES, OrderStatus.REFUNDED)


class StarsPaid(StrEnum):
    """Чем кончилась оплата звёздами (`successful_payment`)."""

    CREDITED = "credited"  # засчитана: доступ выдан или выдаётся, клиенту написал зачёт
    ALREADY_CREDITED = "already_credited"  # тот же платёж пришёл ещё раз — ничего не сделано
    RETURNED = "returned"  # не подошла к заказу — звёзды вернули клиенту
    NOT_RETURNED = "not_returned"  # не подошла, а вернуть не вышло — тревога


class StarsMismatch(StrEnum):
    """Почему оплату звёздами не засчитали (`alerts.stars_reason.*`)."""

    UNKNOWN_ORDER = "unknown_order"  # нет такого заказа звёздами у этого клиента
    OTHER_CHARGE = "other_charge"  # заказ уже оплачен другим платежом
    NOT_CREDITED = "not_credited"  # зачёт отказал (`PaymentMismatch`)


@dataclass(frozen=True)
class StarsInvoice:
    """Счёт в звёздах, который бот отправит клиенту (`sendInvoice`)."""

    order_id: int
    tariff: Tariff
    stars: int
    payload: str  # номер заказа — вернётся в `pre_checkout_query` и `successful_payment`


@dataclass(frozen=True)
class StarsPayment:
    """Оплата из `successful_payment`: кто, по какому счёту, сколько и номер платежа."""

    telegram_id: int
    payload: str
    currency: str
    total_amount: int
    charge_id: str  # `telegram_payment_charge_id` — по нему же возврат


class StarsPayments:
    """Счёт, проверка перед оплатой и зачёт оплаты звёздами."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        orders: OrderService,
        crediting: CreditingService,
        refunds: PaymentRefunds,  # возврат звёзд, которые не подошли к заказу
        events: EventLog,
        alerter: Alerter,
        admin_texts: Texts,
        clock: Clock,
        rate: Decimal,  # курс звезды в рублях (`stars_rub_rate` из tariffs.yaml)
    ) -> None:
        self._session_factory = session_factory
        self._orders = orders
        self._crediting = crediting
        self._refunds = refunds
        self._events = events
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._clock = clock
        self._rate = rate

    def price(self, tariff: Tariff) -> int:
        """Цена тарифа в звёздах."""
        return stars_price(tariff.price, self._rate)

    # --- Счёт ---

    async def invoice(self, client: Client, tariff_id: str) -> StarsInvoice:
        """Новый заказ звёздами и данные счёта. Ошибки — как у `OrderService.stars_order`."""
        created = await self._orders.stars_order(client, tariff_id)
        stars = stars_price(created.tariff.price, self._rate)
        logger.info("Заказ %s: счёт на %d звёзд", created.order_id, stars)
        return StarsInvoice(created.order_id, created.tariff, stars, str(created.order_id))

    # --- Проверка перед оплатой (10 секунд, без запросов наружу) ---

    async def pre_checkout(
        self, telegram_id: int, payload: str, currency: str, total_amount: int
    ) -> StarsRefusal | None:
        """Можно ли оплатить счёт. `None` — можно."""
        async with self._session_factory() as session:
            order = await _stars_order(session, telegram_id, payload)
        view = None if order is None else self._view(order)
        refusal = pre_checkout_refusal(view, currency, total_amount, utc_now(self._clock))
        if refusal is not None:
            logger.info("Счёт «%s» клиента %s не оплатить: %s", payload, telegram_id, refusal)
        return refusal

    def _view(self, order: Order) -> StarsOrder:
        return StarsOrder(
            awaiting_payment=order.status is OrderStatus.CREATED,
            paid=order.status in PAID_STATUSES,
            created_at=order.created_at,
            expected_stars=stars_price(order.price, self._rate),
        )

    # --- Оплата пришла ---

    async def paid(self, payment: StarsPayment) -> StarsPaid:
        """Засчитать оплату звёздами ровно один раз. Не подошла к заказу — вернуть звёзды.

        Ошибку базы бросает дальше: оплату тогда разбирает администратор по тревоге.
        """
        async with self._session_factory() as session:
            order = await _stars_order(session, payment.telegram_id, payment.payload)
        if order is None or payment.currency != STARS_CURRENCY:
            return await self._return(payment, StarsMismatch.UNKNOWN_ORDER, None)
        if not await self._attach(order.id, payment.charge_id):
            return await self._return(payment, StarsMismatch.OTHER_CHARGE, order)
        try:
            result = await self._crediting.credit(order.id, self._state(order, payment))
        except PaymentMismatch as error:
            logger.error("Заказ %s: оплата звёздами не засчитана: %s", order.id, error)
            return await self._return(payment, StarsMismatch.NOT_CREDITED, order)
        if result.outcome is PaymentOutcome.ALREADY_CREDITED:
            return StarsPaid.ALREADY_CREDITED
        return StarsPaid.CREDITED

    async def _attach(self, order_id: int, charge_id: str) -> bool:
        """Записать номер платежа в заказ. `False` — у заказа уже другой платёж."""
        async with self._session_factory() as session:
            attached = await session.execute(
                update(Order)
                .where(
                    Order.id == order_id,
                    Order.payment_provider == STARS_PROVIDER,
                    Order.payment_id.is_(None),
                )
                .values(payment_id=charge_id)
            )
            await session.commit()
            if affected_rows(attached) == 1:
                return True
            current = await session.scalar(select(Order.payment_id).where(Order.id == order_id))
        return current == charge_id  # тот же платёж пришёл ещё раз

    def _state(self, order: Order, payment: StarsPayment) -> PaymentState:
        """Оплата звёздами глазами зачёта: рубли заказа, которые она покрывает."""
        expected = stars_price(order.price, self._rate)
        return PaymentState(
            payment_id=payment.charge_id,
            status=PaymentStatus.SUCCEEDED,
            amount=paid_rubles(order.price, expected, payment.total_amount),
            currency=RUB,
            order_id=order.id,
            test=False,
            paid_at=utc_now(self._clock),
            refunded=Decimal(0),
            stars=payment.total_amount,
        )

    # --- Оплата не подошла к заказу ---

    async def _return(
        self, payment: StarsPayment, reason: StarsMismatch, order: Order | None
    ) -> StarsPaid:
        """Вернуть звёзды клиенту, записать в журнал и поднять тревогу."""
        logger.warning(
            "Оплата звёздами %s клиента %s не засчитана (%s) — возвращаем",
            payment.charge_id,
            payment.telegram_id,
            reason,
        )
        paid = PaidPayment(STARS_PROVIDER, payment.charge_id, payment.telegram_id)
        error: str | None = None
        try:
            await self._refunds.refund(
                paid,
                stars_in_rubles(payment.total_amount, self._rate),
                f"stars-return-{payment.charge_id}",
            )
        except PaymentError as failure:
            error = f"{type(failure).__name__}: {failure}"
            logger.error("Звёзды по платежу %s не вернулись: %s", payment.charge_id, error)
        await self._record_return(payment, reason, order, error)
        await self._alert_return(payment, reason, error)
        return StarsPaid.RETURNED if error is None else StarsPaid.NOT_RETURNED

    async def _record_return(
        self,
        payment: StarsPayment,
        reason: StarsMismatch,
        order: Order | None,
        error: str | None,
    ) -> None:
        async with self._session_factory() as session:
            client_id = await session.scalar(
                select(Client.id).where(Client.telegram_id == payment.telegram_id)
            )
            await self._events.record(
                session,
                EventKind.STARS_PAYMENT_RETURNED,
                client_id=client_id,
                order_id=None if order is None else order.id,
                details={
                    "reason": reason.value,
                    "payload": payment.payload,
                    "charge_id": payment.charge_id,
                    "stars": payment.total_amount,
                    "returned": error is None,
                    "error": error,
                },
            )
            await session.commit()

    async def _alert_return(
        self, payment: StarsPayment, reason: StarsMismatch, error: str | None
    ) -> None:
        texts = self._admin_texts
        values: dict[str, object] = {
            "stars": payment.total_amount,
            "telegram_id": payment.telegram_id,
            "payload": payment.payload,
            "reason": texts.get(f"alerts.stars_reason.{reason.value}"),
        }
        if error is None:
            text = texts.get("alerts.stars_returned", **values)
        else:
            text = texts.get(
                "alerts.stars_not_returned", charge_id=payment.charge_id, error=error, **values
            )
        await self._alerter.raise_alert(f"stars_returned:{payment.charge_id}", text)


async def _stars_order(session: AsyncSession, telegram_id: int, payload: str) -> Order | None:
    """Заказ звёздами из счёта — только среди заказов того, кто платит (пересланный счёт
    чужому не подойдёт). `payload` — номер заказа; не номер — заказа нет."""
    if not payload.isdecimal():
        return None
    return await session.scalar(
        select(Order)
        .join(Client, Client.id == Order.client_id)
        .where(
            Order.id == int(payload),
            Order.payment_provider == STARS_PROVIDER,
            Client.telegram_id == telegram_id,
        )
    )
