"""Тарифы, заказ и платёж (задача 12; решение 04, правила 3 и 11; аудит §4.3, §4.5).

Клиент выбирает тариф → бот создаёт **заказ** с ценой и днями из `tariffs.yaml`
(кнопка передаёт только id тарифа) → создаёт **платёж** у платёжки и отдаёт ссылку.

- Ссылка живёт час. Тот же тариф, пока ссылка жива, — та же ссылка, новый платёж не создаётся.
  Другой тариф — новый заказ и платёж.
- Скрытый тариф (тест за 15 ₽) — только администраторам; проверка здесь, а не в клавиатуре.
- Новых платежей у одного клиента — не больше `NEW_PAYMENTS_LIMIT` за `NEW_PAYMENTS_WINDOW`
  (аудит R29: каждое «Купить» — запрос к платёжке от нашего имени).
- Платёжка не ответила — заказ остаётся без платежа; следующее нажатие повторяет запрос
  с тем же ключом идемпотентности, поэтому «потерянный» платёж не задвоится.

Засчитывает оплату задача 13, уведомления и «Проверить оплату» — задача 14.

Двойная оплата (задача 14, уязвимость 1 карты оплаты): прежде чем дать ссылку — новую
или ту же, — бот спрашивает платёжку о незасчитанных заказах клиента за сутки
(`EarlierPayments.find_paid`). Нашлась оплата — она засчитывается, ссылки нет
(`EarlierPaymentFound`). Платёжка не ответила — ссылки тоже нет (`PaymentUnavailable`):
иначе клиент, у которого просто потерялось уведомление, заплатил бы второй раз.

Порядок замков: замок заказов клиента (здесь) → замок срока (`term_locks`, его берёт зачёт
внутри `find_paid`). Обратного порядка нет: зачёт и выдача замок заказов не берут.

Оплата выключена (`PAYMENTS_MODE=off`, задача 29) — платёжки нет: витрина та же,
но `checkout` отказывает до любых записей в базу и запросов к платёжке.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Tariff, Texts
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Order
from tsproxy.domain.constants import MONTH_DAYS, PAYMENT_LINK_LIFETIME
from tsproxy.domain.money import discount_percent
from tsproxy.domain.payments import can_reuse_payment, payment_link_expires_at
from tsproxy.integrations.payments_types import CreatedPayment, PaymentProvider, PaymentRequest
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.locks import KeyedLocks

logger = logging.getLogger(__name__)

# Решение владельца 2026-10-25: 10 новых платежей за 10 минут, дальше — сообщение «подождите»
NEW_PAYMENTS_LIMIT = 10
NEW_PAYMENTS_WINDOW = timedelta(minutes=10)


class TariffUnavailable(Exception):
    """Такого тарифа нет или он скрыт от этого клиента (устаревшая или подменённая кнопка)."""


class TooManyPayments(Exception):
    """Клиент слишком часто создаёт новые платежи — подождать."""


class PaymentsDisabled(Exception):
    """Оплата выключена (`PAYMENTS_MODE=off`): заказы и платежи не создаются."""


class EarlierPaymentFound(Exception):
    """У клиента нашлась незасчитанная оплата прошлого заказа — засчитана, ссылки нет."""

    def __init__(self, order_id: int) -> None:
        super().__init__(f"нашлась оплата заказа {order_id}")
        self.order_id = order_id


class EarlierPayments(Protocol):
    """Проверка прошлых оплат клиента перед новой ссылкой. Настоящая — `PaymentChecks`."""

    async def find_paid(self, client_id: int) -> int | None:
        """Засчитать оплату прошлого заказа, если она есть; вернуть номер этого заказа.

        Платёжка не ответила — `PaymentUnavailable`.
        """
        ...


@dataclass(frozen=True)
class TariffOffer:
    """Тариф на витрине: скидка — от цены месяца, её бот считает сам."""

    tariff: Tariff
    discount_percent: int


@dataclass(frozen=True)
class Checkout:
    """Что показать клиенту на экране оплаты."""

    order_id: int
    tariff: Tariff
    payment_url: str
    expires_at: datetime
    reused: bool  # та же ссылка, что и в прошлый раз


class OrderService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        payments: PaymentProvider | None,  # None — оплата выключена (задача 29)
        events: EventLog,
        texts: Texts,
        tariffs: Sequence[Tariff],
        admin_ids: frozenset[int],
        clock: Clock,
        locks: KeyedLocks,
        earlier_payments: EarlierPayments,
    ) -> None:
        self._session_factory = session_factory
        self._payments = payments
        self._earlier_payments = earlier_payments
        self._events = events
        self._texts = texts
        self._tariffs = tuple(tariffs)
        self._admin_ids = admin_ids
        self._clock = clock
        self._locks = locks

    @property
    def payments_enabled(self) -> bool:
        """Можно ли сейчас купить: подключена ли платёжка."""
        return self._payments is not None

    # --- Витрина ---

    def offers(self, telegram_id: int) -> tuple[TariffOffer, ...]:
        """Тарифы, которые видит клиент. Скрытые — только администраторам."""
        monthly = next(
            (t.price for t in self._tariffs if not t.hidden and t.days == MONTH_DAYS), None
        )
        return tuple(
            TariffOffer(
                tariff,
                0 if monthly is None else discount_percent(tariff.days, tariff.price, monthly),
            )
            for tariff in self._tariffs
            if self._visible(tariff, telegram_id)
        )

    def _visible(self, tariff: Tariff, telegram_id: int) -> bool:
        return not tariff.hidden or telegram_id in self._admin_ids

    def _tariff_for(self, telegram_id: int, tariff_id: str) -> Tariff:
        """Тариф по id из кнопки — только из белого списка `tariffs.yaml`."""
        for tariff in self._tariffs:
            if tariff.id == tariff_id and self._visible(tariff, telegram_id):
                return tariff
        raise TariffUnavailable(f"тариф {tariff_id!r} недоступен клиенту {telegram_id}")

    # --- Заказ и платёж ---

    async def checkout(self, client: Client, tariff_id: str) -> Checkout:
        """Ссылка на оплату тарифа: та же, если она ещё жива, иначе — новый заказ и платёж.

        Ошибки платёжки (`PaymentError`) не глотаются: экран решает, что сказать клиенту.
        Оплата выключена — `PaymentsDisabled` раньше всего остального: ни заказа, ни платежа.
        Нашлась оплата прошлого заказа — `EarlierPaymentFound`: она засчитана, ссылки нет.
        """
        payments = self._payments
        if payments is None:
            raise PaymentsDisabled(f"оплата выключена, клиент {client.telegram_id}")
        tariff = self._tariff_for(client.telegram_id, tariff_id)
        async with self._locks.hold(client.id):
            paid_order_id = await self._earlier_payments.find_paid(client.id)
            if paid_order_id is not None:
                raise EarlierPaymentFound(paid_order_id)
            now = utc_now(self._clock)
            async with self._session_factory() as session:
                order = await self._open_order(session, client.id, tariff)
                if order is not None and order.payment_url is not None:
                    if can_reuse_payment(order.payment_url_expires_at, now):
                        logger.info("Заказ %s: та же ссылка на оплату", order.id)
                        return _checkout(order, tariff, reused=True)
                    order = None  # ссылка истекает — нужна новая
                if order is None or not _retry_allowed(order, now):
                    await self._check_rate(session, client.id, now)
                    order = await self._new_order(session, client.id, tariff, now)
            created = await payments.create_payment(
                PaymentRequest(
                    order_id=order.id,
                    amount=order.price,
                    description=self._texts.get("payment.description", title=tariff.title),
                    idempotency_key=order.idempotency_key,
                )
            )
            return await self._attach_payment(order.id, tariff, payments.name, created, now)

    async def _open_order(
        self, session: AsyncSession, client_id: int, tariff: Tariff
    ) -> Order | None:
        """Последний неоплаченный заказ клиента на этот тариф — если цена в файле не менялась."""
        order = await session.scalar(
            select(Order)
            .where(
                Order.client_id == client_id,
                Order.tariff_id == tariff.id,
                Order.status == OrderStatus.CREATED,
            )
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(1)
        )
        if order is None or (order.price, order.days) != (tariff.price, tariff.days):
            return None
        return order

    async def _check_rate(self, session: AsyncSession, client_id: int, now: datetime) -> None:
        recent = await session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.client_id == client_id, Order.created_at > now - NEW_PAYMENTS_WINDOW)
        )
        if recent is not None and recent >= NEW_PAYMENTS_LIMIT:
            logger.warning("Клиент %s: %d новых заказов за окно — ждём", client_id, recent)
            raise TooManyPayments(f"клиент {client_id}: {recent} новых платежей подряд")

    async def _new_order(
        self, session: AsyncSession, client_id: int, tariff: Tariff, now: datetime
    ) -> Order:
        order = Order(
            client_id=client_id,
            tariff_id=tariff.id,
            price=tariff.price,
            days=tariff.days,
            status=OrderStatus.CREATED,
            created_at=now,
        )
        session.add(order)
        await session.flush()
        await self._events.record(
            session,
            EventKind.ORDER_CREATED,
            client_id=client_id,
            order_id=order.id,
            details={"tariff_id": tariff.id, "price": str(tariff.price), "days": tariff.days},
        )
        await session.commit()
        logger.info("Заказ %s: клиент %s, тариф %s", order.id, client_id, tariff.id)
        return order

    async def _attach_payment(
        self,
        order_id: int,
        tariff: Tariff,
        provider: str,
        created: CreatedPayment,
        now: datetime,
    ) -> Checkout:
        """Записать платёж в заказ. Срок ссылки — от момента до запроса: с запасом."""
        async with self._session_factory() as session:
            order = await session.get_one(Order, order_id)
            order.payment_provider = provider
            order.payment_id = created.payment_id
            order.payment_url = created.url
            order.payment_url_expires_at = payment_link_expires_at(now, created.expires_at)
            await self._events.record(
                session,
                EventKind.PAYMENT_CREATED,
                client_id=order.client_id,
                order_id=order.id,
                details={"provider": provider, "payment_id": created.payment_id},
            )
            await session.commit()
        logger.info("Заказ %s: платёж %s создан", order_id, created.payment_id)
        return _checkout(order, tariff, reused=False)


def _retry_allowed(order: Order, now: datetime) -> bool:
    """Заказ без платежа (платёжка не ответила) — повторить запрос, пока заказ свежий."""
    return order.payment_id is None and order.created_at > now - PAYMENT_LINK_LIFETIME


def _checkout(order: Order, tariff: Tariff, *, reused: bool) -> Checkout:
    if order.payment_url is None or order.payment_url_expires_at is None:
        raise ValueError(f"заказ {order.id} без ссылки на оплату")
    return Checkout(order.id, tariff, order.payment_url, order.payment_url_expires_at, reused)
