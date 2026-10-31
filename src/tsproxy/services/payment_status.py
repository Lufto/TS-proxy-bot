"""Статус оплаты изменился после оплаты: спор через банк, возврат, проверка (задача 15;
решение владельца 2026-10-30 «спор через банк»).

Platega сообщает о каждом изменении колбэком (`CONFIRMED`, `CANCELED`, `CHARGEBACKED`,
`PARTIALLY_REFUNDED`, `DISPUTED`). Сюда попадает уже **перепроверенный** статус
(`get_payment`) — телу уведомления бот не верит. Что делаем:

- **Деньги вернули целиком** (`CHARGEBACKED` → `PaymentStatus.REFUNDED`):
  - это наш возврат (кнопка «↩️ Вернуть», задача 21: по заказу есть начатый возврат) —
    тихо: возврат ждал итога, теперь деньги вернулись — снимаем дни (`settle_review`);
  - возврат сделал продавец, но не через бота (в колбэке есть `refundId` — например, владелец
    вернул в кабинете, когда API отказал) — только тревога «возврат мимо бота»: доступ
    не трогаем, клиенту не пишем про спор (решение агента: не наказывать клиента за наш
    же возврат);
  - иначе — **спор через банк**: отключить доступ в Remnawave, сообщение клиенту,
    тревога с данными клиента и оплаты, запись в журнал событий.
- **Любой другой неожиданный статус** (`DISPUTED`, `PARTIALLY_REFUNDED`, незнакомый,
  «отменён» у засчитанного заказа, возврат по заказу, который мы не засчитывали) — только
  тревога «статус оплаты неожиданно изменился: был …, стал …». Возврат спора назад
  в «оплачен» (`DISPUTED → CONFIRMED`) — тоже такая тревога: спор снят.

Каждое изменение пишется в журнал событий. Тот же статус ещё раз (повтор колбэка, фоновая
проверка) — ничего не делаем: Platega шлёт колбэк до 3 раз и в любом порядке.
"""

import logging
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.enums import OrderStatus, RefundStatus
from tsproxy.db.models import Client, Event, Order, RefundRequest
from tsproxy.db.repository import CREDITED_ORDER_STATUSES
from tsproxy.domain.money import format_rubles
from tsproxy.integrations.payments_types import (
    PaymentNotice,
    PaymentState,
    PaymentStatus,
    RefundOutcome,
)
from tsproxy.integrations.remnawave_types import AccountNotFound, RemnawaveClient, RemnawaveError
from tsproxy.services.alerts import Alerter
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.locks import KeyedLocks

logger = logging.getLogger(__name__)

# Возврат, который начал бот и который ещё ждёт денег от платёжки
AWAITING_REFUND = (RefundStatus.APPROVED, RefundStatus.IN_REVIEW)
# Возврат бота, по которому деньги уже вернулись
FINISHED_REFUND = (RefundStatus.REFUNDED, RefundStatus.COMPLETED)
# События, после которых повтор того же «деньги вернули» ничего не меняет
REFUND_SEEN_EVENTS = (EventKind.PAYMENT_CHARGEBACK, EventKind.PAYMENT_REFUNDED_OUTSIDE_BOT)
# Подробности события изменения статуса
STATUS_DETAIL = "status"  # наш статус (`PaymentStatus`) — по нему узнаём повтор
PROVIDER_STATUS_DETAIL = "provider_status"  # как назвала платёжка


class StatusChange(StrEnum):
    """Что бот сделал с изменившимся статусом."""

    REPEAT = "repeat"  # этот статус уже видели — ничего
    OUR_REFUND = "our_refund"  # наш возврат дошёл — дни сняты (или снимаются)
    REFUNDED_OUTSIDE_BOT = "refunded_outside_bot"  # возврат мимо бота — тревога
    CHARGEBACK = "chargeback"  # спор через банк — доступ отключён, тревога
    CHANGED = "changed"  # другой неожиданный статус — тревога


class AccessAfterDispute(StrEnum):
    """Что с доступом клиента после спора через банк."""

    DISABLED = "disabled"  # отключён
    NO_ACCOUNT = "no_account"  # прокси-аккаунта нет — отключать нечего
    FAILED = "failed"  # панель не ответила — отключить вручную


class RefundReviews(Protocol):
    """Итог возврата, который ждал платёжку. Настоящий — `RefundService.settle_review`."""

    async def settle_review(self, request_id: int, outcome: RefundOutcome) -> object: ...


class DisputeMessages(Protocol):
    """Сообщение клиенту об отключении доступа из-за спора (в боте — `bot.payments`)."""

    async def payment_disputed(self, telegram_id: int) -> None: ...


class PaymentStatusChanges:
    """Разобрать перепроверенный статус оплаты, который изменился после оплаты."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        refunds: RefundReviews,
        events: EventLog,
        alerter: Alerter,
        admin_texts: Texts,
        messages: DisputeMessages,
        term_locks: KeyedLocks,  # общая блокировка срока клиента: не спорить с выдачей доступа
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._refunds = refunds
        self._events = events
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._messages = messages
        self._term_locks = term_locks

    async def changed(
        self, order: Order, state: PaymentState, notice: PaymentNotice | None
    ) -> StatusChange:
        """Статус платежа заказа по ответу платёжки. `notice` — уведомление, если пришло оно.

        Для заказа, который ждёт оплаты, «оплачен», «ждёт» и «отменён» сюда не приходят —
        их разбирает обычная проверка оплат.
        """
        credited = order.status in CREDITED_ORDER_STATUSES or order.status is OrderStatus.REFUNDED
        if credited and state.status is PaymentStatus.REFUNDED:
            return await self._refunded(order, state, notice)
        return await self._unexpected(order, state, credited=credited)

    # --- Деньги вернули целиком ---

    async def _refunded(
        self, order: Order, state: PaymentState, notice: PaymentNotice | None
    ) -> StatusChange:
        async with self._session_factory() as session:
            refund = await _bot_refund(session, order.id)
            seen = await _seen_refund(session, order.id)
        if refund is not None:
            if refund.status in AWAITING_REFUND:
                logger.info("Заказ %s: наш возврат №%s дошёл до клиента", order.id, refund.id)
                await self._refunds.settle_review(refund.id, RefundOutcome.SUCCEEDED)
            return StatusChange.OUR_REFUND
        if seen:
            return StatusChange.REPEAT
        if notice is not None and notice.refund_id is not None:
            await self._refunded_outside_bot(order, state, notice.refund_id)
            return StatusChange.REFUNDED_OUTSIDE_BOT
        await self._chargeback(order, state)
        return StatusChange.CHARGEBACK

    async def _refunded_outside_bot(
        self, order: Order, state: PaymentState, refund_id: str
    ) -> None:
        """Продавец вернул деньги не через бота (кабинет Platega): доступ не трогаем, тревога."""
        logger.warning("Заказ %s: возврат %s мимо бота", order.id, refund_id)
        async with self._session_factory() as session:
            client = await session.get_one(Client, order.client_id)
            await self._events.record(
                session,
                EventKind.PAYMENT_REFUNDED_OUTSIDE_BOT,
                client_id=order.client_id,
                order_id=order.id,
                details={"payment_id": state.payment_id, "refund_id": refund_id},
            )
            await session.commit()
        await self._alerter.raise_alert(
            f"payment_refunded_outside_bot:{order.id}",
            self._admin_texts.get(
                "alerts.payment_refunded_outside_bot", **self._values(order, state, client)
            ),
        )

    async def _chargeback(self, order: Order, state: PaymentState) -> None:
        """Спор через банк: отключить доступ, написать клиенту, тревога, журнал."""
        async with self._session_factory() as session:
            client = await session.get_one(Client, order.client_id)
        logger.warning("Заказ %s: спор через банк, платёж %s", order.id, state.payment_id)
        access, error = await self._disable(client)
        async with self._session_factory() as session:
            await self._events.record(
                session,
                EventKind.PAYMENT_CHARGEBACK,
                client_id=client.id,
                order_id=order.id,
                details={
                    "payment_id": state.payment_id,
                    PROVIDER_STATUS_DETAIL: state.status_name,
                    "access": access.value,
                },
            )
            await session.commit()
        await self._messages.payment_disputed(client.telegram_id)
        access_text = self._admin_texts.get(f"alerts.chargeback_access.{access.value}", error=error)
        await self._alerter.raise_alert(
            f"payment_chargeback:{order.id}",
            self._admin_texts.get(
                "alerts.payment_chargeback",
                access=access_text,
                **self._values(order, state, client),
            ),
        )

    async def _disable(self, client: Client) -> tuple[AccessAfterDispute, str]:
        """Отключить прокси-аккаунт клиента. Панель не ответила — тревога скажет «вручную»."""
        try:
            async with self._term_locks.hold(client.id):
                await self._remnawave.disable_account(client.telegram_id)
        except AccountNotFound:
            return AccessAfterDispute.NO_ACCOUNT, ""
        except RemnawaveError as error:
            logger.error("Клиент %s: доступ после спора не отключён: %s", client.id, error)
            return AccessAfterDispute.FAILED, f"{type(error).__name__}: {error}"
        return AccessAfterDispute.DISABLED, ""

    # --- Другой неожиданный статус ---

    async def _unexpected(
        self, order: Order, state: PaymentState, *, credited: bool
    ) -> StatusChange:
        """Тревога «был …, стал …» — один раз на каждое изменение."""
        baseline = PaymentStatus.SUCCEEDED if credited else PaymentStatus.PENDING
        async with self._session_factory() as session:
            last = await _last_status(session, order.id)
            previous, previous_name = last if last is not None else (baseline, None)
            if previous is state.status:
                return StatusChange.REPEAT
            client = await session.get_one(Client, order.client_id)
            await self._events.record(
                session,
                EventKind.PAYMENT_STATUS_CHANGED,
                client_id=order.client_id,
                order_id=order.id,
                details={
                    "payment_id": state.payment_id,
                    "from": previous.value,
                    STATUS_DETAIL: state.status.value,
                    PROVIDER_STATUS_DETAIL: state.status_name,
                },
            )
            await session.commit()
        logger.warning(
            "Заказ %s: статус оплаты %s → %s", order.id, previous.value, state.status_name
        )
        await self._alerter.raise_alert(
            f"payment_status_changed:{order.id}:{state.status.value}",
            self._admin_texts.get(
                "alerts.payment_status_changed",
                was=self._status_text(previous, previous_name),
                now=self._status_text(state.status, state.status_name),
                **self._values(order, state, client),
            ),
        )
        return StatusChange.CHANGED

    def _status_text(self, status: PaymentStatus, provider_name: str | None) -> str:
        label = self._admin_texts.get(f"payments.status.{status.value}")
        if provider_name is None or provider_name == status.value:
            return label
        return f"{label} ({provider_name})"

    def _values(self, order: Order, state: PaymentState, client: Client) -> dict[str, object]:
        no_value = self._admin_texts.get("admin.no_value")
        return {
            "order_id": order.id,
            "payment_id": state.payment_id,
            "amount": format_rubles(order.paid_amount or order.price),
            "telegram_id": client.telegram_id,
            "name": client.name or no_value,
            "username": client.username or no_value,
        }


async def _bot_refund(session: AsyncSession, order_id: int) -> RefundRequest | None:
    """Последний возврат бота по заказу, который не закончился отказом платёжки."""
    return await session.scalar(
        select(RefundRequest)
        .where(
            RefundRequest.order_id == order_id,
            RefundRequest.status.in_((*AWAITING_REFUND, *FINISHED_REFUND)),
        )
        .order_by(RefundRequest.id.desc())
        .limit(1)
    )


async def _seen_refund(session: AsyncSession, order_id: int) -> bool:
    found = await session.scalar(
        select(Event.id)
        .where(Event.order_id == order_id, Event.kind.in_([k.value for k in REFUND_SEEN_EVENTS]))
        .limit(1)
    )
    return found is not None


async def _last_status(
    session: AsyncSession, order_id: int
) -> tuple[PaymentStatus, str | None] | None:
    """Последний записанный статус оплаты заказа (из журнала) и его имя у платёжки."""
    details = await session.scalar(
        select(Event.details)
        .where(Event.order_id == order_id, Event.kind == EventKind.PAYMENT_STATUS_CHANGED.value)
        .order_by(Event.id.desc())
        .limit(1)
    )
    if details is None:
        return None
    return PaymentStatus(details[STATUS_DETAIL]), details.get(PROVIDER_STATUS_DETAIL)
