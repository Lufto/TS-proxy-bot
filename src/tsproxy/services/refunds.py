"""Возвраты денег (задача 21; решения задачи 12 и владельца 2026-10-25 «Карта оплаты»).

Путь: клиент жмёт «↩️ Вернуть деньги» → бот считает сумму (`domain.refund`) → заявка
в базе и карточка в теме «↩️ Возвраты» → администратор одобряет или отклоняет →
платёжка возвращает деньги → бот укорачивает подписку.

Возвраты — только через бота (решение владельца, п. 4). Защиты (payment-journeys §4 №8):

- **Двойное «Одобрить»** (два администратора, двойное нажатие): заявка переходит
  «запрошен → одобрен» одним `UPDATE … WHERE статус «запрошен»`. Изменилась 0 строк —
  решение уже принято, деньги второй раз не возвращаются. Ключ идемпотентности у
  платёжки — номер заявки, так что и повтор после сбоя не вернёт деньги дважды.
- **Деньги вернулись, а срок не укорочен** (панель не ответила): заявка остаётся
  «деньги возвращены», тревога, фоновая задача повторяет каждую минуту. Бесплатного
  доступа молча не остаётся.
- **Срок укорачивается на неиспользованные дни этого заказа** от текущего срока, а не
  до «начала периода»: дни следующих заказов остаются (Г5). Срок «до X» считается один
  раз и записывается в журнал до запроса к панели: повтор не укоротит дважды.
- Каждое изменение срока — под общей блокировкой срока клиента (`term_locks`), аккаунт
  перечитывается из панели уже под ней.

Случайная вторая оплата (решение владельца, п. 1): задача 14 замечает её и пишет событие
`DOUBLE_PAYMENT_SUSPECTED` с номером второго заказа. По такому заказу клиент может
попросить полный возврат в течение 7 дней, вне правил «30 дней» и «раз в полгода»;
такой возврат и не считается «прошлым возвратом» для правила «раз в полгода».
Решает всё равно администратор.

При `PAYMENTS_MODE=off` платёжки нет — вернуть деньги через бота нельзя, клиент видит
это честно (`RefundRefusal.PAYMENTS_OFF`).
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.enums import OrderStatus, RefundStatus
from tsproxy.db.models import Client, Event, Order, RefundRequest
from tsproxy.db.repository import affected_rows
from tsproxy.domain.money import format_rubles, paid_duration
from tsproxy.domain.refund import (
    REFUND_END_MARGIN,
    RefundDecision,
    RefundDenial,
    RefundKind,
    RefundReason,
    decide_double_payment_refund,
    decide_refund,
    limit_to_term,
    next_refund_at,
    term_after_refund,
)
from tsproxy.domain.refund import RefundRequest as RefundFacts
from tsproxy.integrations.payments_types import (
    PaymentError,
    PaymentProvider,
    PaymentUnavailable,
    RefundOutcome,
)
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    ProxyAccount,
    RemnawaveClient,
    RemnawaveError,
)
from tsproxy.services.admin import AdminActionKind, same_second
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.locks import KeyedLocks

logger = logging.getLogger(__name__)

# Заявка ещё не закончена: второй запрос клиента не принимаем
OPEN_STATUSES = (RefundStatus.REQUESTED, RefundStatus.APPROVED, RefundStatus.REFUNDED)
# Деньги вернули (или возвращают): такой возврат считается «прошлым» для правила полугода
APPROVED_STATUSES = (RefundStatus.APPROVED, RefundStatus.REFUNDED, RefundStatus.COMPLETED)
# Фоновая задача доделывает: «одобрен» — вернуть деньги, «деньги возвращены» — укоротить срок
UNFINISHED_STATUSES = (RefundStatus.APPROVED, RefundStatus.REFUNDED)
# Поля подробностей событий возврата
REQUEST_DETAIL = "request_id"
KIND_DETAIL = "kind"
# Сколько незаконченных заявок фоновая задача берёт за проход
RETRY_BATCH_SIZE = 20


class RefundRefusal(StrEnum):
    """Почему клиенту нельзя запросить возврат."""

    PAYMENTS_OFF = "payments_off"  # платёжка не подключена (`PAYMENTS_MODE=off`)
    NO_PAID_ORDER = "no_paid_order"  # нет оплаты с выданным доступом
    ALREADY_REQUESTED = "already_requested"  # прошлая заявка ещё не закончена
    NOT_DOUBLE_PAYMENT = "not_double_payment"  # заказ не отмечен как случайная вторая оплата
    WINDOW_PASSED = "window_passed"
    TOO_FREQUENT = "too_frequent"
    NOTHING_UNUSED = "nothing_unused"


_DENIAL_REFUSALS = {
    RefundDenial.WINDOW_PASSED: RefundRefusal.WINDOW_PASSED,
    RefundDenial.TOO_FREQUENT: RefundRefusal.TOO_FREQUENT,
    RefundDenial.NOTHING_UNUSED: RefundRefusal.NOTHING_UNUSED,
}


class RefundRejection(StrEnum):
    """Почему администратор отклонил возврат. Пишется в `decision_reason`, клиент
    получает объяснение по этой причине (тексты `refund.rejected.*`)."""

    PROVIDER_BLOCK = "provider_block"  # доступ ограничил интернет-провайдер
    STATE_BLOCK = "state_block"  # доступ ограничил государственный орган
    CHANGED_MIND = "changed_mind"  # просто передумал — для этого есть пробный
    OTHER = "other"  # другое — подробности клиенту пишет поддержка


class ApprovalOutcome(StrEnum):
    """Чем кончилось «✅ Одобрить»."""

    COMPLETED = "completed"  # деньги возвращены, срок укорочен
    TERM_PENDING = "term_pending"  # деньги возвращены, срок укоротится повтором (тревога)
    PROVIDER_PENDING = "provider_pending"  # платёжка не ответила — повторим сами
    PROVIDER_REJECTED = "provider_rejected"  # платёжка отказала — тревога, вручную
    ALREADY_DECIDED = "already_decided"  # решение уже принято (второе нажатие)
    PAYMENTS_OFF = "payments_off"  # платёжки нет — вернуть нельзя
    NOT_FOUND = "not_found"


_STATUS_OUTCOMES = {
    RefundStatus.COMPLETED: ApprovalOutcome.COMPLETED,
    RefundStatus.REFUNDED: ApprovalOutcome.TERM_PENDING,
    RefundStatus.APPROVED: ApprovalOutcome.PROVIDER_PENDING,
    RefundStatus.FAILED: ApprovalOutcome.PROVIDER_REJECTED,
}


class RejectionOutcome(StrEnum):
    REJECTED = "rejected"
    ALREADY_DECIDED = "already_decided"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class RefundQuote:
    """Сколько клиенту вернут за эту оплату — показать до отправки заявки."""

    order_id: int
    kind: RefundKind
    paid_at: datetime
    paid_amount: Decimal
    unused_days: int
    amount: Decimal


@dataclass(frozen=True)
class RefundRefused:
    """Возврат запросить нельзя. `available_at` — когда можно снова (раз в полгода)."""

    refusal: RefundRefusal
    available_at: datetime | None = None


@dataclass(frozen=True)
class RefundCard:
    """Заявка для администраторов: карточка в теме «↩️ Возвраты»."""

    request_id: int
    status: RefundStatus
    kind: RefundKind
    telegram_id: int
    name: str | None
    username: str | None
    order_id: int
    tariff_id: str
    paid_at: datetime
    paid_amount: Decimal
    unused_days: int
    amount: Decimal


@dataclass(frozen=True)
class ApprovalResult:
    outcome: ApprovalOutcome
    card: RefundCard | None = None
    expire_at: datetime | None = None  # срок после укорачивания; None — доступ выключен


@dataclass(frozen=True)
class RejectionResult:
    outcome: RejectionOutcome
    card: RefundCard | None = None


class RefundBoard(Protocol):
    """Карточка новой заявки в теме «↩️ Возвраты» (в боте — `bot.refunds`)."""

    async def request_created(self, card: RefundCard) -> None: ...


class RefundMessages(Protocol):
    """Сообщения клиенту о решении (в боте — `bot.refunds`). Ошибки Telegram — внутри."""

    async def refund_completed(
        self, telegram_id: int, amount: Decimal, expire_at: datetime | None
    ) -> None:
        """Деньги возвращены, срок укорочен. `expire_at=None` — доступ выключен."""
        ...

    async def refund_rejected(self, telegram_id: int, reason: RefundRejection) -> None: ...


@dataclass(frozen=True)
class _PlannedTerm:
    """Срок до и после укорачивания — записан в журнал до запроса к панели."""

    expire_before: datetime
    expire_after: datetime
    end_access: bool


class RefundService:
    """Заявки на возврат: расчёт, заявка, решение администратора, возврат и новый срок."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        payments: PaymentProvider | None,
        events: EventLog,
        alerter: Alerter,
        admin_texts: Texts,
        board: RefundBoard,
        messages: RefundMessages,
        clock: Clock,
        term_locks: KeyedLocks,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._payments = payments
        self._events = events
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._board = board
        self._messages = messages
        self._clock = clock
        # общая блокировка срока клиента (зачёт оплаты, ручная выдача, льготный)
        self._term_locks = term_locks
        # своя очередь: заявки одного клиента и обработка одной заявки — по одной
        self._locks = KeyedLocks()

    # --- Клиент: расчёт и заявка ---

    async def quote(self, client: Client) -> RefundQuote | RefundRefused:
        """Обычный возврат за последнюю оплату: сумма или причина отказа. Ничего не меняет.

        Срок читается из панели — `RemnawaveUnavailable` уходит вызывающему.
        """
        return await self._quote(client, None)

    async def quote_double_payment(
        self, client: Client, order_id: int
    ) -> RefundQuote | RefundRefused:
        """Полный возврат случайной второй оплаты `order_id` (заказ этого клиента)."""
        return await self._quote(client, order_id)

    async def request(self, client: Client) -> RefundCard | RefundRefused:
        """Создать заявку на обычный возврат и отправить её в «↩️ Возвраты»."""
        return await self._request(client, None)

    async def request_double_payment(
        self, client: Client, order_id: int
    ) -> RefundCard | RefundRefused:
        """Создать заявку на полный возврат случайной второй оплаты `order_id`."""
        return await self._request(client, order_id)

    async def _quote(
        self, client: Client, double_order_id: int | None
    ) -> RefundQuote | RefundRefused:
        if self._payments is None:
            return RefundRefused(RefundRefusal.PAYMENTS_OFF)
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            if await _has_open_request(session, client.id):
                return RefundRefused(RefundRefusal.ALREADY_REQUESTED)
            if double_order_id is None:
                order = await _latest_granted_order(session, client.id)
                last_refund_at = await _last_standard_refund_at(session, client.id)
            else:
                order = await _granted_order(session, client.id, double_order_id)
                last_refund_at = None
            if order is None:
                return RefundRefused(RefundRefusal.NO_PAID_ORDER)
            if double_order_id is not None and not await _double_payment_marked(session, order.id):
                return RefundRefused(RefundRefusal.NOT_DOUBLE_PAYMENT)
        kind = RefundKind.STANDARD if double_order_id is None else RefundKind.DOUBLE_PAYMENT
        decision = _decide(order, kind, now, last_refund_at)
        account = await self._account(client.telegram_id)
        decision = limit_to_term(decision, _term_end(account, now), now)
        if decision.denial is not None:
            available_at = None
            if decision.denial is RefundDenial.TOO_FREQUENT and last_refund_at is not None:
                available_at = next_refund_at(last_refund_at)
            return RefundRefused(_DENIAL_REFUSALS[decision.denial], available_at)
        paid_at, paid_amount, _ = _paid(order)
        return RefundQuote(
            order.id, kind, paid_at, paid_amount, decision.unused_days, decision.amount
        )

    async def _request(
        self, client: Client, double_order_id: int | None
    ) -> RefundCard | RefundRefused:
        async with self._locks.hold(("client", client.id)):
            quote = await self._quote(client, double_order_id)
            if isinstance(quote, RefundRefused):
                return quote
            async with self._session_factory() as session:
                row = RefundRequest(
                    order_id=quote.order_id,
                    client_id=client.id,
                    requested_at=utc_now(self._clock),
                    unused_days=quote.unused_days,
                    amount=quote.amount,
                    status=RefundStatus.REQUESTED,
                )
                session.add(row)
                await session.flush()
                await self._events.record(
                    session,
                    EventKind.REFUND_REQUESTED,
                    client_id=client.id,
                    order_id=quote.order_id,
                    details={
                        REQUEST_DETAIL: row.id,
                        KIND_DETAIL: quote.kind.value,
                        "amount": str(quote.amount),
                        "unused_days": quote.unused_days,
                        "paid_amount": str(quote.paid_amount),
                    },
                )
                await session.commit()
                request_id = row.id
        logger.info("Клиент %s: заявка на возврат %s", client.id, request_id)
        card = await self.card(request_id)
        if card is None:
            raise LookupError(f"заявка {request_id} пропала сразу после записи")
        await self._post(card)
        return card

    async def _post(self, card: RefundCard) -> None:
        """Карточка в «↩️ Возвраты». Не ушла — тревога: заявка в базе, её покажет `/refunds`."""
        try:
            await self._board.request_created(card)
        # Ловим всё: заявка уже записана, а ошибки Telegram и сети бывают разных типов.
        # Не прячем: лог с трассировкой и тревога, чтобы заявку не забыли
        except Exception:
            logger.exception("Заявка на возврат %s не отправлена в тему", card.request_id)
            await self._alerter.raise_alert(
                f"refund_not_posted:{card.request_id}",
                self._admin_texts.get("alerts.refund_not_posted", request_id=card.request_id),
            )

    # --- Администратор ---

    async def card(self, request_id: int) -> RefundCard | None:
        """Заявка для показа администратору. `None` — такой нет."""
        async with self._session_factory() as session:
            row = await session.get(RefundRequest, request_id)
            if row is None:
                return None
            return await _card(session, row)

    async def open_requests(self) -> list[RefundCard]:
        """Заявки, по которым ещё нет решения, — старые сначала (`/refunds`)."""
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(RefundRequest)
                    .where(RefundRequest.status == RefundStatus.REQUESTED)
                    .order_by(RefundRequest.id)
                )
            )
            return [await _card(session, row) for row in rows]

    async def approve(self, admin_id: int, request_id: int) -> ApprovalResult:
        """Одобрить: вернуть деньги через платёжку и укоротить подписку.

        Решение принимается один раз (атомарный переход). Сбои платёжки и панели не
        теряют заявку: фоновая задача `retry_unfinished` доделает её сама.
        """
        if self._payments is None:
            return ApprovalResult(ApprovalOutcome.PAYMENTS_OFF, await self.card(request_id))
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            row = await session.get(RefundRequest, request_id)
            if row is None:
                return ApprovalResult(ApprovalOutcome.NOT_FOUND)
            if not await _claim(session, request_id, RefundStatus.APPROVED, admin_id, now):
                await session.rollback()
                return ApprovalResult(ApprovalOutcome.ALREADY_DECIDED, await self.card(request_id))
            details = {REQUEST_DETAIL: request_id, "amount": str(row.amount)}
            await self._record_decision(session, row, admin_id, EventKind.REFUND_APPROVED, details)
            await session.commit()
        logger.info("Администратор %s одобрил возврат %s", admin_id, request_id)
        expire_at = await self._process(request_id)
        card = await self.card(request_id)
        if card is None:
            raise LookupError(f"заявка {request_id} пропала")
        return ApprovalResult(_STATUS_OUTCOMES[card.status], card, expire_at)

    async def reject(
        self, admin_id: int, request_id: int, reason: RefundRejection
    ) -> RejectionResult:
        """Отклонить заявку и объяснить клиенту причину. Решение — один раз."""
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            row = await session.get(RefundRequest, request_id)
            if row is None:
                return RejectionResult(RejectionOutcome.NOT_FOUND)
            claimed = await _claim(
                session, request_id, RefundStatus.REJECTED, admin_id, now, reason.value
            )
            if not claimed:
                await session.rollback()
                return RejectionResult(
                    RejectionOutcome.ALREADY_DECIDED, await self.card(request_id)
                )
            details = {REQUEST_DETAIL: request_id, "reason": reason.value}
            await self._record_decision(session, row, admin_id, EventKind.REFUND_REJECTED, details)
            telegram_id = await session.scalar(
                select(Client.telegram_id).where(Client.id == row.client_id)
            )
            await session.commit()
        if telegram_id is None:
            raise LookupError(f"клиента заявки {request_id} нет")
        logger.info("Администратор %s отклонил возврат %s: %s", admin_id, request_id, reason)
        await self._messages.refund_rejected(telegram_id, reason)
        return RejectionResult(RejectionOutcome.REJECTED, await self.card(request_id))

    async def _record_decision(
        self,
        session: AsyncSession,
        row: RefundRequest,
        admin_id: int,
        kind: EventKind,
        details: dict[str, Any],
    ) -> None:
        """Событие решения и, как у любого действия администратора, `ADMIN_ACTION`."""
        action = (
            AdminActionKind.REFUND_APPROVED
            if kind is EventKind.REFUND_APPROVED
            else AdminActionKind.REFUND_REJECTED
        )
        common = {"client_id": row.client_id, "order_id": row.order_id, "admin_id": admin_id}
        await self._events.record(session, kind, details=details, **common)
        await self._events.record(
            session, EventKind.ADMIN_ACTION, details={"action": action.value, **details}, **common
        )

    # --- Возврат денег и новый срок ---

    async def retry_unfinished(self) -> None:
        """Фоновая задача (раз в минуту): доделать одобренные заявки после сбоев."""
        async with self._session_factory() as session:
            ids = list(
                await session.scalars(
                    select(RefundRequest.id)
                    .where(RefundRequest.status.in_(UNFINISHED_STATUSES))
                    .order_by(RefundRequest.id)
                    .limit(RETRY_BATCH_SIZE)
                )
            )
        for request_id in ids:
            await self._process(request_id)

    async def _process(self, request_id: int) -> datetime | None:
        """Довести одобренную заявку как можно дальше. Возвращает срок после укорачивания."""
        async with self._locks.hold(("request", request_id)):
            async with self._session_factory() as session:
                row = await session.get_one(RefundRequest, request_id)
                order = await session.get_one(Order, row.order_id)
                client = await session.get_one(Client, row.client_id)
            if row.status is RefundStatus.APPROVED:
                if not await self._return_money(row, order):
                    return None
                row.status = RefundStatus.REFUNDED
            if row.status is RefundStatus.REFUNDED:
                return await self._shorten(row, client)
            return None

    async def _return_money(self, row: RefundRequest, order: Order) -> bool:
        """Попросить платёжку вернуть деньги. `True` — платёжка приняла возврат."""
        if self._payments is None:
            logger.warning("Заявка %s одобрена, а платёжки нет — ждём её", row.id)
            return False
        if order.payment_id is None:
            raise ValueError(f"заказ {order.id} оплачен без номера платежа")
        try:
            refund = await self._payments.refund(
                order.payment_id, row.amount, refund_idempotency_key(row.id)
            )
        except PaymentUnavailable as error:
            logger.warning("Возврат %s: платёжка не ответила: %s", row.id, error)
            await self._alert("refund_provider_unavailable", row, error=str(error))
            return False
        except PaymentError as error:
            await self._fail(row, f"{type(error).__name__}: {error}")
            return False
        if refund.outcome is RefundOutcome.CANCELED:
            await self._fail(row, f"возврат {refund.refund_id} отменён платёжкой")
            return False
        async with self._session_factory() as session:
            moved = await session.execute(
                update(RefundRequest)
                .where(RefundRequest.id == row.id, RefundRequest.status == RefundStatus.APPROVED)
                .values(status=RefundStatus.REFUNDED)
            )
            if affected_rows(moved) == 0:
                raise RuntimeError(f"заявка {row.id} изменилась во время возврата")
            await session.execute(
                update(Order).where(Order.id == order.id).values(status=OrderStatus.REFUNDED)
            )
            await self._events.record(
                session,
                EventKind.REFUND_COMPLETED,
                client_id=row.client_id,
                order_id=order.id,
                details={
                    REQUEST_DETAIL: row.id,
                    "refund_id": refund.refund_id,
                    "amount": str(refund.amount),
                    "outcome": refund.outcome.value,
                },
            )
            await session.commit()
        logger.info("Возврат %s: платёжка вернула %s", row.id, refund.amount)
        return True

    async def _fail(self, row: RefundRequest, error: str) -> None:
        """Платёжка отказала: повтор не поможет — «платёжка отказала» и тревога."""
        logger.error("Возврат %s: платёжка отказала: %s", row.id, error)
        async with self._session_factory() as session:
            await session.execute(
                update(RefundRequest)
                .where(RefundRequest.id == row.id, RefundRequest.status == RefundStatus.APPROVED)
                .values(status=RefundStatus.FAILED)
            )
            await self._events.record(
                session,
                EventKind.REFUND_FAILED,
                client_id=row.client_id,
                order_id=row.order_id,
                details={REQUEST_DETAIL: row.id, "error": error},
            )
            await session.commit()
        await self._alert("refund_provider_rejected", row, error=error)

    async def _shorten(self, row: RefundRequest, client: Client) -> datetime | None:
        """Укоротить срок на возвращённые дни. Сбой панели — тревога, повтор фоновой задачей."""
        try:
            async with self._term_locks.hold(client.id):
                after = await self._apply_term(row, client)
        except RemnawaveError as error:
            logger.warning("Возврат %s: срок не укорочен: %s", row.id, error)
            await self._alert("refund_term_pending", row, error=f"{type(error).__name__}: {error}")
            return None
        async with self._session_factory() as session:
            await session.execute(
                update(RefundRequest)
                .where(RefundRequest.id == row.id, RefundRequest.status == RefundStatus.REFUNDED)
                .values(status=RefundStatus.COMPLETED)
            )
            await session.commit()
        logger.info("Возврат %s закончен", row.id)
        await self._messages.refund_completed(client.telegram_id, row.amount, after)
        return after

    async def _apply_term(self, row: RefundRequest, client: Client) -> datetime | None:
        """Под блокировкой срока: свежий аккаунт, срок «до X» из журнала или новый расчёт."""
        try:
            account = await self._remnawave.get_account(client.telegram_id)
        except AccountNotFound:
            # аккаунта нет — укорачивать нечего, доступа и так нет
            await self._record_term(row, None, ended=True)
            return None
        now = utc_now(self._clock)
        planned = await self._planned_term(row)
        if planned is not None and not _plan_still_valid(planned, account):
            # После расчёта срок изменили (оплата, администратор): считаем заново от
            # нынешнего — бесплатных дней не остаётся, но пусть администратор проверит
            await self._alert("refund_term_changed", row, error=account.expire_at.isoformat())
            planned = None
        if planned is None:
            term = term_after_refund(account.expire_at, row.unused_days, now)
            planned = _PlannedTerm(account.expire_at, term.expire_at, term.end_access)
            await self._record_plan(row, planned)
        account, ended = await self._set_term(client.telegram_id, account, planned, now)
        await self._record_term(row, account, ended=ended)
        return None if ended else account.expire_at

    async def _set_term(
        self, telegram_id: int, account: ProxyAccount, planned: _PlannedTerm, now: datetime
    ) -> tuple[ProxyAccount, bool]:
        """Поставить срок «до X» (только раньше нынешнего) и выключить доступ, если надо.

        Возвращает аккаунт и «доступ выключен».
        """
        target, end_access = planned.expire_after, planned.end_access
        if target <= now + REFUND_END_MARGIN:
            # повтор спустя долгое время: посчитанный срок уже прошёл — доступ кончился
            target, end_access = now + REFUND_END_MARGIN, True
        if account.expire_at > target and not same_second(account.expire_at, target):
            account = await self._remnawave.set_expiry(telegram_id, target)
        if end_access and account.status is not AccountStatus.DISABLED:
            account = await self._remnawave.disable_account(telegram_id)
        return account, end_access

    async def _planned_term(self, row: RefundRequest) -> _PlannedTerm | None:
        async with self._session_factory() as session:
            details = await _request_event_details(session, row, EventKind.REFUND_TERM_PLANNED)
        if details is None:
            return None
        return _PlannedTerm(
            datetime.fromisoformat(details["expire_before"]),
            datetime.fromisoformat(details["expire_after"]),
            bool(details["end_access"]),
        )

    async def _record_plan(self, row: RefundRequest, planned: _PlannedTerm) -> None:
        """Записать срок «до X» до запроса к панели: повтор после сбоя не укоротит дважды."""
        async with self._session_factory() as session:
            await self._events.record(
                session,
                EventKind.REFUND_TERM_PLANNED,
                client_id=row.client_id,
                order_id=row.order_id,
                details={
                    REQUEST_DETAIL: row.id,
                    "unused_days": row.unused_days,
                    "expire_before": planned.expire_before.isoformat(),
                    "expire_after": planned.expire_after.isoformat(),
                    "end_access": planned.end_access,
                },
            )
            await session.commit()

    async def _record_term(
        self, row: RefundRequest, account: ProxyAccount | None, *, ended: bool
    ) -> None:
        async with self._session_factory() as session:
            await self._events.record(
                session,
                EventKind.REFUND_TERM_SHORTENED,
                client_id=row.client_id,
                order_id=row.order_id,
                details={
                    REQUEST_DETAIL: row.id,
                    "no_account": account is None,
                    "expire_after": None if account is None else account.expire_at.isoformat(),
                    "access_ended": ended,
                },
            )
            await session.commit()

    async def _alert(self, kind: str, row: RefundRequest, *, error: str) -> None:
        await self._alerter.raise_alert(
            f"{kind}:{row.id}",
            self._admin_texts.get(
                f"alerts.{kind}",
                request_id=row.id,
                order_id=row.order_id,
                amount=format_rubles(row.amount),
                error=error,
            ),
        )

    async def _account(self, telegram_id: int) -> ProxyAccount | None:
        try:
            return await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            return None  # аккаунта нет — и возвращать не за что


def refund_idempotency_key(request_id: int) -> str:
    """Ключ идемпотентности возврата у платёжки — номер заявки: повтор не вернёт дважды."""
    return f"refund-request-{request_id}"


def _paid(order: Order) -> tuple[datetime, Decimal, datetime]:
    """Когда и сколько оплачено и до какой даты продлено — у выданного заказа есть всё."""
    if order.paid_at is None or order.paid_amount is None or order.grant_target_expire_at is None:
        raise ValueError(f"заказ {order.id} выдан без оплаты или срока")
    return order.paid_at, order.paid_amount, order.grant_target_expire_at


def _decide(
    order: Order, kind: RefundKind, now: datetime, last_refund_at: datetime | None
) -> RefundDecision:
    paid_at, paid_amount, period_end = _paid(order)
    duration = paid_duration(order.days, order.price, paid_amount)
    if kind is RefundKind.DOUBLE_PAYMENT:
        return decide_double_payment_refund(now, paid_at, paid_amount, duration)
    # Причину решает администратор; клиенту считаем по единственной, при которой возврат
    # положен, — «не работало по нашей вине»
    return decide_refund(
        RefundFacts(
            now=now,
            reason=RefundReason.SERVICE_FAULT,
            paid_at=paid_at,
            paid_amount=paid_amount,
            period_start=period_end - duration,
            period_end=period_end,
            last_refund_at=last_refund_at,
        )
    )


def _term_end(account: ProxyAccount | None, now: datetime) -> datetime:
    """До какого момента у клиента есть доступ. Нет аккаунта или доступ выключен — сейчас."""
    if account is None or account.status is AccountStatus.DISABLED:
        return now
    return account.expire_at


async def _claim(
    session: AsyncSession,
    request_id: int,
    status: RefundStatus,
    admin_id: int,
    now: datetime,
    reason: str | None = None,
) -> bool:
    """«Запрошен → решение» одним `UPDATE`. `False` — решение уже принято."""
    result = await session.execute(
        update(RefundRequest)
        .where(RefundRequest.id == request_id, RefundRequest.status == RefundStatus.REQUESTED)
        .values(status=status, decided_by_admin_id=admin_id, decided_at=now, decision_reason=reason)
    )
    return affected_rows(result) == 1


async def _has_open_request(session: AsyncSession, client_id: int) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    RefundRequest.client_id == client_id,
                    RefundRequest.status.in_(OPEN_STATUSES),
                )
            )
        )
    )


async def _latest_granted_order(session: AsyncSession, client_id: int) -> Order | None:
    """Последняя оплата с выданным доступом: её дни — в конце срока."""
    return await session.scalar(
        select(Order)
        .where(Order.client_id == client_id, Order.status == OrderStatus.ACCESS_GRANTED)
        .order_by(Order.paid_at.desc(), Order.id.desc())
        .limit(1)
    )


async def _granted_order(session: AsyncSession, client_id: int, order_id: int) -> Order | None:
    """Заказ этого клиента с выданным доступом — чужой номер из кнопки ничего не даст."""
    return await session.scalar(
        select(Order).where(
            Order.id == order_id,
            Order.client_id == client_id,
            Order.status == OrderStatus.ACCESS_GRANTED,
        )
    )


async def _double_payment_marked(session: AsyncSession, order_id: int) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    Event.order_id == order_id,
                    Event.kind == EventKind.DOUBLE_PAYMENT_SUSPECTED.value,
                )
            )
        )
    )


async def _double_payment_request_ids(session: AsyncSession, client_id: int) -> set[int]:
    """Заявки клиента на возврат случайной второй оплаты — по событию «запрос на возврат»."""
    details = await session.scalars(
        select(Event.details).where(
            Event.client_id == client_id, Event.kind == EventKind.REFUND_REQUESTED.value
        )
    )
    return {
        int(item[REQUEST_DETAIL])
        for item in details
        if item is not None and item.get(KIND_DETAIL) == RefundKind.DOUBLE_PAYMENT.value
    }


async def _last_standard_refund_at(session: AsyncSession, client_id: int) -> datetime | None:
    """Когда клиенту одобрили прошлый обычный возврат (для правила «раз в полгода»)."""
    rows = await session.execute(
        select(RefundRequest.id, RefundRequest.decided_at).where(
            RefundRequest.client_id == client_id,
            RefundRequest.status.in_(APPROVED_STATUSES),
            RefundRequest.decided_at.is_not(None),
        )
    )
    double = await _double_payment_request_ids(session, client_id)
    moments = [decided_at for request_id, decided_at in rows if request_id not in double]
    return max(moments, default=None)


async def _request_kind(session: AsyncSession, row: RefundRequest) -> RefundKind:
    details = await _request_event_details(session, row, EventKind.REFUND_REQUESTED)
    if details is None:
        return RefundKind.STANDARD
    return RefundKind(details.get(KIND_DETAIL, RefundKind.STANDARD.value))


async def _request_event_details(
    session: AsyncSession, row: RefundRequest, kind: EventKind
) -> dict[str, Any] | None:
    """Подробности первого события вида `kind` этой заявки."""
    details = await session.scalars(
        select(Event.details)
        .where(Event.order_id == row.order_id, Event.kind == kind.value)
        .order_by(Event.id)
    )
    return next(
        (item for item in details if item is not None and item.get(REQUEST_DETAIL) == row.id),
        None,
    )


def _plan_still_valid(planned: _PlannedTerm, account: ProxyAccount) -> bool:
    """Срок в панели тот, от которого считали, или уже не позже посчитанного."""
    return (
        same_second(account.expire_at, planned.expire_before)
        or account.expire_at <= planned.expire_after
        or same_second(account.expire_at, planned.expire_after)
    )


async def _card(session: AsyncSession, row: RefundRequest) -> RefundCard:
    order = await session.get_one(Order, row.order_id)
    client = await session.get_one(Client, row.client_id)
    if order.paid_at is None or order.paid_amount is None:
        raise ValueError(f"заказ {order.id} в заявке на возврат без оплаты")
    return RefundCard(
        request_id=row.id,
        status=row.status,
        kind=await _request_kind(session, row),
        telegram_id=client.telegram_id,
        name=client.name,
        username=client.username,
        order_id=order.id,
        tariff_id=order.tariff_id,
        paid_at=order.paid_at,
        paid_amount=order.paid_amount,
        unused_days=row.unused_days,
        amount=row.amount,
    )
