"""Возвраты денег (задача 21; решение владельца 2026-10-30).

**Через поддержку.** У клиента кнопки «вернуть деньги» нет: он пишет в поддержку,
администратор решает сам и нажимает «↩️ Вернуть» в карточке клиента (тема обращения или
`/client`). В списке — оплаты за последние 30 дней (`domain.refund`). Бот возвращает **всю
сумму** оплаты через платёжку и снимает со срока всё, что эта оплата дала (`paid_duration`).

**Случайная вторая оплата** (задача 14 отмечает её полем `possible_double_of` в событии
оплаты): клиент сам, без администратора, может вернуть её кнопкой в течение 7 дней или
нажать «Оставить». Оставил или 7 дней прошли — только через поддержку. Администраторам —
сообщение в «💰 Оплаты» в обоих случаях.

Защиты:

- **Одна оплата — один возврат.** Возврат начинается одним `UPDATE заказ «доступ выдан» →
  «возвращён»`: изменилась 0 строк — оплату уже возвращают, второй раз деньги не уйдут
  (двойное нажатие, два администратора). Ключ идемпотентности у платёжки — номер возврата:
  повтор после сбоя тоже не вернёт дважды. Платёжка отказала — заказ снова «доступ выдан».
- **Платёжка не ответила** — возврат остаётся «начат», фоновая задача повторяет раз в минуту.
- **Платёжка отправила возврат на проверку** (у Platega — `manualControlRequired`) —
  «на проверке у платёжки»; дни не снимаем, пока деньги не вернулись. Итог приходит позже
  (`settle_review`, вызовет уведомление платёжки — задача 15).
- **Деньги вернулись, а срок не укорочен** (панель не ответила) — «деньги возвращены»,
  тревога, фоновая задача повторяет. Срок «до X» считается один раз и записывается в журнал
  до запроса к панели: повтор не укоротит дважды.
- Каждое изменение срока — под общей блокировкой срока клиента (`term_locks`), аккаунт
  перечитывается из панели уже под ней.
- **Подписку перенесли на другой аккаунт** после этой оплаты — её дни уже не здесь:
  возврат через бота не делаем, иначе ушли бы и деньги, и дни (решение агента).

**Чем платили — тем и возвращаем** (задача 33): деньги возвращает `RefundRouter` по платёжке
заказа — СБП через платёжку, звёзды Telegram через `refundStarPayment`. Сервис по способу
оплаты не ветвится. Способа нет (оплата выключена, платёжку сменили) — «оплата выключена».
Суммы в сообщениях — тем, чем платили: «150 ₽» или «100 ⭐» (`content.amounts.paid_text`).

Возвраты, решённые раньше 2026-10-30 по старому замыслу (запрос клиента), в базе не встречаются:
бот ещё не запускался.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.amounts import paid_text
from tsproxy.content.loader import Texts
from tsproxy.db.enums import OrderStatus, RefundStatus
from tsproxy.db.models import Client, Event, Order, RefundRequest
from tsproxy.db.repository import affected_rows
from tsproxy.domain.constants import ONE_DAY
from tsproxy.domain.money import paid_duration
from tsproxy.domain.refund import (
    REFUND_END_MARGIN,
    double_payment_refund_open,
    refund_list_since,
    refund_window_open,
    term_after_refund,
)
from tsproxy.integrations.payments_types import (
    PaidPayment,
    PaymentError,
    PaymentUnavailable,
    Refund,
    RefundOutcome,
)
from tsproxy.integrations.refunds import RefundRouter
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    ProxyAccount,
    RemnawaveClient,
    RemnawaveError,
)
from tsproxy.services.admin import AdminActionKind, same_second
from tsproxy.services.admin_topics import AdminTopic
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.crediting import DOUBLE_DETAIL, paid_stars
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.locks import KeyedLocks

logger = logging.getLogger(__name__)

# Возврат начат и не закончен: оплату нельзя вернуть второй раз, подписку — перенести
OPEN_STATUSES = (RefundStatus.APPROVED, RefundStatus.IN_REVIEW, RefundStatus.REFUNDED)
# Фоновая задача доделывает: «начат» — вернуть деньги, «деньги возвращены» — укоротить срок
UNFINISHED_STATUSES = (RefundStatus.APPROVED, RefundStatus.REFUNDED)
# Из этих состояний платёжка может сообщить итог: деньги возвращены или отказ
AWAITING_PROVIDER = (RefundStatus.APPROVED, RefundStatus.IN_REVIEW)
# Поле подробностей событий возврата — номер возврата
REQUEST_DETAIL = "request_id"
# Сколько незаконченных возвратов фоновая задача берёт за проход
RETRY_BATCH_SIZE = 20


class RefundKind(StrEnum):
    """Кто решил вернуть. Пишется в `decision_reason` и в подробности событий."""

    SUPPORT = "support"  # администратор по обращению в поддержку
    DOUBLE_PAYMENT = "double_payment"  # клиент сам — случайная вторая оплата


class RefundRefusal(StrEnum):
    """Почему эту оплату нельзя вернуть через бота сейчас."""

    # способ оплаты заказа не подключён (`PAYMENTS_MODE=off` или платёжку сменили)
    PAYMENTS_OFF = "payments_off"
    CLIENT_NOT_FOUND = "client_not_found"  # не клиент бота
    ORDER_NOT_FOUND = "order_not_found"  # нет такой оплаты у этого клиента
    NOT_GRANTED = "not_granted"  # доступ по оплате ещё не выдан (выдача идёт или сбой)
    WINDOW_PASSED = "window_passed"  # старше 30 дней (у клиента — 7 дней)
    ALREADY_REFUNDED = "already_refunded"  # уже возвращена или возвращается
    TRANSFERRED = "transferred"  # подписку перенесли на другой аккаунт после этой оплаты
    # оплату оспорили через банк: деньги клиенту вернул банк, дни уже сняты (задача 15)
    DISPUTED = "disputed"
    NOT_DOUBLE_PAYMENT = "not_double_payment"  # клиент сам — только случайную вторую
    KEPT = "kept"  # клиент нажал «Оставить» — теперь только через поддержку


class RefundProgress(StrEnum):
    """Докуда дошёл возврат."""

    COMPLETED = "completed"  # деньги возвращены, срок укорочен
    TERM_PENDING = "term_pending"  # деньги возвращены, срок укоротится повтором (тревога)
    IN_REVIEW = "in_review"  # платёжка проверяет возврат — итог придёт позже
    PROVIDER_PENDING = "provider_pending"  # платёжка не ответила — повторим сами
    FAILED = "failed"  # платёжка отказала — тревога, вручную


_STATUS_PROGRESS = {
    RefundStatus.COMPLETED: RefundProgress.COMPLETED,
    RefundStatus.REFUNDED: RefundProgress.TERM_PENDING,
    RefundStatus.IN_REVIEW: RefundProgress.IN_REVIEW,
    RefundStatus.APPROVED: RefundProgress.PROVIDER_PENDING,
    RefundStatus.FAILED: RefundProgress.FAILED,
}


@dataclass(frozen=True)
class AdminRef:
    """Кто из администраторов вернул: Telegram ID и имя — для журнала и темы."""

    telegram_id: int
    name: str


@dataclass(frozen=True)
class RefundablePayment:
    """Оплата в списке «↩️ Вернуть». `blocker` — почему вернуть нельзя (None — можно);
    `refund_status` — что с последним возвратом этой оплаты (None — не возвращали)."""

    order_id: int
    tariff_id: str
    days: int
    paid_at: datetime
    amount: Decimal
    duration: timedelta  # сколько срока дала оплата — столько и снимется
    refund_status: RefundStatus | None
    blocker: RefundRefusal | None
    stars: int | None = None  # оплачено звёздами Telegram — сколько (задача 33)


@dataclass(frozen=True)
class RefundRefused:
    refusal: RefundRefusal


@dataclass(frozen=True)
class RefundResult:
    """Итог нажатия. `expire_at` — срок после возврата (None — доступ выключен или срок
    ещё не укорочен: смотреть `progress`)."""

    request_id: int
    order_id: int
    telegram_id: int
    amount: Decimal
    progress: RefundProgress
    expire_at: datetime | None
    stars: int | None = None  # оплачено звёздами Telegram — сколько (задача 33)


class RefundMessages(Protocol):
    """Сообщение клиенту о законченном возврате (в боте — `bot.refunds`). Ошибки Telegram
    реализация разбирает сама: возврат к этому моменту уже сделан."""

    async def refund_completed(
        self, telegram_id: int, amount: Decimal, expire_at: datetime | None, *, stars: int | None
    ) -> None:
        """Деньги возвращены, срок укорочен. `expire_at=None` — доступ выключен.

        `stars` — оплата была звёздами Telegram: вернулись звёзды, а не `amount` рублей.
        """
        ...


@dataclass(frozen=True)
class _PlannedTerm:
    """Срок до и после укорачивания — записан в журнал до запроса к панели."""

    expire_before: datetime
    expire_after: datetime
    end_access: bool


class RefundService:
    """Возврат оплаты: решение (администратор или клиент), деньги через платёжку, новый срок."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        refunds: RefundRouter,  # чем платили — тем и возвращаем (СБП, звёзды)
        events: EventLog,
        alerter: Alerter,
        payments_topic: AdminTopic,  # «💰 Оплаты»
        admin_texts: Texts,
        messages: RefundMessages,
        clock: Clock,
        term_locks: KeyedLocks,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._refunds = refunds
        self._events = events
        self._alerter = alerter
        self._payments_topic = payments_topic
        self._admin_texts = admin_texts
        self._messages = messages
        self._clock = clock
        # общая блокировка срока клиента (зачёт оплаты, ручная выдача, льготный)
        self._term_locks = term_locks
        # своя очередь: начало возврата одной оплаты и обработка одного возврата — по одному
        self._locks = KeyedLocks()

    # --- Администратор: «↩️ Вернуть» ---

    async def refundable_payments(self, telegram_id: int) -> list[RefundablePayment] | None:
        """Оплаты клиента за последние 30 дней, новые сверху. `None` — не клиент бота."""
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            client = await _client(session, telegram_id)
            if client is None:
                return None
            orders = await session.scalars(
                select(Order)
                .where(
                    Order.client_id == client.id,
                    Order.paid_at.is_not(None),
                    Order.paid_at >= refund_list_since(now),
                )
                .order_by(Order.paid_at.desc(), Order.id.desc())
            )
            return [
                await self._describe(session, client, order, RefundKind.SUPPORT, now)
                for order in orders
            ]

    async def payment(self, telegram_id: int, order_id: int) -> RefundablePayment | RefundRefused:
        """Оплата для подтверждения «вернуть?» администратору. Ничего не меняет."""
        return await self._payment(telegram_id, order_id, RefundKind.SUPPORT)

    async def refund_by_admin(
        self, admin: AdminRef, telegram_id: int, order_id: int
    ) -> RefundResult | RefundRefused:
        """Вернуть всю сумму оплаты и снять её дни — после подтверждения администратором."""
        return await self._refund(telegram_id, order_id, RefundKind.SUPPORT, admin)

    # --- Клиент: случайная вторая оплата ---

    async def double_payment(
        self, telegram_id: int, order_id: int
    ) -> RefundablePayment | RefundRefused:
        """Вторая оплата для подтверждения «вернуть?» клиенту. Ничего не меняет."""
        return await self._payment(telegram_id, order_id, RefundKind.DOUBLE_PAYMENT)

    async def refund_double_payment(
        self, telegram_id: int, order_id: int
    ) -> RefundResult | RefundRefused:
        """Клиент сам возвращает случайную вторую оплату (7 дней, без администратора)."""
        return await self._refund(telegram_id, order_id, RefundKind.DOUBLE_PAYMENT, None)

    async def keep_double_payment(self, telegram_id: int, order_id: int) -> RefundRefused | None:
        """«Оставить — продлить подписку»: вернуть потом можно только через поддержку.

        `None` — оставлено. После 7 дней кнопка уже ничего не решает: ничего не пишем.
        """
        now = utc_now(self._clock)
        async with self._locks.hold(("order", order_id)), self._session_factory() as session:
            client = await _client(session, telegram_id)
            if client is None:
                return RefundRefused(RefundRefusal.CLIENT_NOT_FOUND)
            order = await _client_order(session, client.id, order_id)
            if order is None or order.paid_at is None:
                return RefundRefused(RefundRefusal.ORDER_NOT_FOUND)
            refusal = await _double_payment_refusal(session, order)
            if refusal is not None:
                return RefundRefused(refusal)
            if order.status is OrderStatus.REFUNDED:
                return RefundRefused(RefundRefusal.ALREADY_REFUNDED)
            if not double_payment_refund_open(order.paid_at, now):
                return None
            await self._events.record(
                session, EventKind.DOUBLE_PAYMENT_KEPT, client_id=client.id, order_id=order.id
            )
            await session.commit()
            stars = await _order_stars(session, order.id)
            text = self._admin_texts.get(
                "refunds.topic.double_kept",
                order_id=order.id,
                amount=paid_text(self._admin_texts, _paid_amount(order), stars),
                **_client_values(self._admin_texts, client),
            )
        logger.info("Клиент %s оставил вторую оплату, заказ %s", client.id, order_id)
        await self._payments_topic.post(text)
        return None

    # --- Платёжка сообщила итог проверки (задача 15) ---

    async def settle_review(self, request_id: int, outcome: RefundOutcome) -> RefundProgress:
        """Итог возврата, отправленного платёжкой на проверку. Повтор безопасен."""
        async with self._locks.hold(("request", request_id)):
            row = await self._row(request_id)
            if row.status in AWAITING_PROVIDER:
                if outcome is RefundOutcome.SUCCEEDED:
                    await self._mark_refunded(row, row.amount, refund_id=None)
                elif outcome is RefundOutcome.CANCELED:
                    await self._fail(row, "платёжка отклонила возврат после проверки")
            progress, _ = await self._continue(request_id)
        if _STATUS_PROGRESS[row.status] is not progress:
            await self._report(request_id, progress)
        return progress

    # --- Фоновая задача ---

    async def retry_unfinished(self) -> None:
        """Раз в минуту: доделать возвраты после сбоев платёжки или панели."""
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
            before = (await self._row(request_id)).status
            async with self._locks.hold(("request", request_id)):
                progress, _ = await self._continue(request_id)
            if _STATUS_PROGRESS[before] is not progress:
                await self._report(request_id, progress)

    # --- Начало возврата ---

    async def _payment(
        self, telegram_id: int, order_id: int, kind: RefundKind
    ) -> RefundablePayment | RefundRefused:
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            client = await _client(session, telegram_id)
            if client is None:
                return RefundRefused(RefundRefusal.CLIENT_NOT_FOUND)
            order = await _client_order(session, client.id, order_id)
            if order is None or order.paid_at is None:
                return RefundRefused(RefundRefusal.ORDER_NOT_FOUND)
            payment = await self._describe(session, client, order, kind, now)
        if payment.blocker is not None:
            return RefundRefused(payment.blocker)
        return payment

    async def _refund(
        self, telegram_id: int, order_id: int, kind: RefundKind, admin: AdminRef | None
    ) -> RefundResult | RefundRefused:
        claimed = await self._claim(telegram_id, order_id, kind, admin)
        if isinstance(claimed, RefundRefused):
            return claimed
        async with self._locks.hold(("request", claimed)):
            progress, expire_at = await self._continue(claimed)
        await self._report(claimed, progress)
        row = await self._row(claimed)
        stars = await self._stars(row.order_id)
        return RefundResult(
            claimed, row.order_id, telegram_id, row.amount, progress, expire_at, stars
        )

    async def _claim(
        self, telegram_id: int, order_id: int, kind: RefundKind, admin: AdminRef | None
    ) -> int | RefundRefused:
        """Начать возврат: заказ «доступ выдан» → «возвращён» одним `UPDATE`, строка возврата
        и события — одной транзакцией. Возвращает номер возврата."""
        now = utc_now(self._clock)
        async with self._locks.hold(("order", order_id)), self._session_factory() as session:
            client = await _client(session, telegram_id)
            if client is None:
                return RefundRefused(RefundRefusal.CLIENT_NOT_FOUND)
            order = await _client_order(session, client.id, order_id)
            if order is None or order.paid_at is None:
                return RefundRefused(RefundRefusal.ORDER_NOT_FOUND)
            refusal = await self._refusal(session, client, order, kind, now)
            if refusal is not None:
                return RefundRefused(refusal)
            moved = await session.execute(
                update(Order)
                .where(Order.id == order.id, Order.status == OrderStatus.ACCESS_GRANTED)
                .values(status=OrderStatus.REFUNDED)
            )
            if affected_rows(moved) == 0:
                await session.rollback()
                return RefundRefused(RefundRefusal.ALREADY_REFUNDED)
            row = RefundRequest(
                order_id=order.id,
                client_id=client.id,
                requested_at=now,
                unused_days=_duration(order) // ONE_DAY,
                amount=_paid_amount(order),
                status=RefundStatus.APPROVED,
                decided_by_admin_id=None if admin is None else admin.telegram_id,
                decision_reason=kind.value,
                decided_at=now,
            )
            session.add(row)
            await session.flush()
            await self._record_start(session, row, kind, admin)
            await session.commit()
            request_id = row.id
        logger.info("Возврат %s заказа %s начат (%s)", request_id, order_id, kind.value)
        return request_id

    async def _record_start(
        self, session: AsyncSession, row: RefundRequest, kind: RefundKind, admin: AdminRef | None
    ) -> None:
        """«Возврат решён» и, если решил администратор, `ADMIN_ACTION` с его именем."""
        details: dict[str, Any] = {
            REQUEST_DETAIL: row.id,
            "kind": kind.value,
            "amount": str(row.amount),
            "days": row.unused_days,
            "admin_name": None if admin is None else admin.name,
        }
        common: dict[str, Any] = {
            "client_id": row.client_id,
            "order_id": row.order_id,
            "admin_id": None if admin is None else admin.telegram_id,
        }
        await self._events.record(session, EventKind.REFUND_APPROVED, details=details, **common)
        if admin is not None:
            await self._events.record(
                session,
                EventKind.ADMIN_ACTION,
                details={"action": AdminActionKind.REFUND.value, **details},
                **common,
            )

    async def _describe(
        self,
        session: AsyncSession,
        client: Client,
        order: Order,
        kind: RefundKind,
        now: datetime,
    ) -> RefundablePayment:
        if order.paid_at is None:
            raise ValueError(f"заказ {order.id} без оплаты")
        return RefundablePayment(
            order_id=order.id,
            tariff_id=order.tariff_id,
            days=order.days,
            paid_at=order.paid_at,
            amount=_paid_amount(order),
            duration=_duration(order),
            refund_status=await _last_refund_status(session, order.id),
            blocker=await self._refusal(session, client, order, kind, now),
            stars=await _order_stars(session, order.id),
        )

    async def _refusal(
        self,
        session: AsyncSession,
        client: Client,
        order: Order,
        kind: RefundKind,
        now: datetime,
    ) -> RefundRefusal | None:
        """Почему эту оплату нельзя вернуть сейчас. `None` — можно."""
        if order.paid_at is None:
            return RefundRefusal.ORDER_NOT_FOUND
        if kind is RefundKind.DOUBLE_PAYMENT:
            refusal = await _double_payment_refusal(session, order)
            if refusal is not None:
                return refusal
        if order.status is OrderStatus.REFUNDED:
            return RefundRefusal.ALREADY_REFUNDED
        if await _disputed(session, order.id):
            return RefundRefusal.DISPUTED
        if order.status is not OrderStatus.ACCESS_GRANTED:
            return RefundRefusal.NOT_GRANTED
        window_open = (
            double_payment_refund_open(order.paid_at, now)
            if kind is RefundKind.DOUBLE_PAYMENT
            else refund_window_open(order.paid_at, now)
        )
        if not window_open:
            return RefundRefusal.WINDOW_PASSED
        if await _transferred_since(session, client, order.paid_at):
            return RefundRefusal.TRANSFERRED
        if not self._refunds.supports(order.payment_provider):
            return RefundRefusal.PAYMENTS_OFF
        return None

    # --- Деньги и срок ---

    async def _continue(self, request_id: int) -> tuple[RefundProgress, datetime | None]:
        """Довести возврат как можно дальше. Вызывать под замком возврата."""
        row = await self._row(request_id)
        if row.status is RefundStatus.APPROVED:
            await self._return_money(row)
            row = await self._row(request_id)
        if row.status is RefundStatus.REFUNDED:
            return await self._shorten(row)
        return _STATUS_PROGRESS[row.status], None

    async def _return_money(self, row: RefundRequest) -> None:
        """Попросить платёжку вернуть всю сумму. Статус меняется по ответу платёжки."""
        async with self._session_factory() as session:
            order = await session.get_one(Order, row.order_id)
            client = await session.get_one(Client, row.client_id)
        if not self._refunds.supports(order.payment_provider):
            logger.warning(
                "Возврат %s начат, а платёжки %s нет — ждём её", row.id, order.payment_provider
            )
            return
        if order.payment_provider is None or order.payment_id is None:
            raise ValueError(f"заказ {order.id} оплачен без номера платежа")
        paid = PaidPayment(order.payment_provider, order.payment_id, client.telegram_id)
        try:
            refund = await self._refunds.refund(paid, row.amount, refund_idempotency_key(row.id))
        except PaymentUnavailable as error:
            logger.warning("Возврат %s: платёжка не ответила: %s", row.id, error)
            await self._alert("refund_provider_unavailable", row, error=str(error))
            return
        except PaymentError as error:
            await self._fail(row, f"{type(error).__name__}: {error}")
            return
        await self._apply_outcome(row, refund)

    async def _apply_outcome(self, row: RefundRequest, refund: Refund) -> None:
        match refund.outcome:
            case RefundOutcome.SUCCEEDED:
                await self._mark_refunded(row, refund.amount, refund.refund_id)
            case RefundOutcome.CANCELED:
                await self._fail(row, f"возврат {refund.refund_id} отменён платёжкой")
            case RefundOutcome.IN_REVIEW | RefundOutcome.PENDING:
                await self._mark_in_review(row, refund)

    async def _mark_in_review(self, row: RefundRequest, refund: Refund) -> None:
        """Платёжка проверяет возврат: дни не снимаем, пока деньги не вернулись."""
        async with self._session_factory() as session:
            await session.execute(
                update(RefundRequest)
                .where(RefundRequest.id == row.id, RefundRequest.status == RefundStatus.APPROVED)
                .values(status=RefundStatus.IN_REVIEW)
            )
            await self._events.record(
                session,
                EventKind.REFUND_IN_REVIEW,
                client_id=row.client_id,
                order_id=row.order_id,
                details={
                    REQUEST_DETAIL: row.id,
                    "refund_id": refund.refund_id,
                    "outcome": refund.outcome.value,
                },
            )
            await session.commit()
        logger.info("Возврат %s: платёжка отправила его на проверку", row.id)

    async def _mark_refunded(
        self, row: RefundRequest, amount: Decimal, refund_id: str | None
    ) -> None:
        """Платёжка вернула деньги: «деньги возвращены» и событие для статистики."""
        async with self._session_factory() as session:
            moved = await session.execute(
                update(RefundRequest)
                .where(RefundRequest.id == row.id, RefundRequest.status.in_(AWAITING_PROVIDER))
                .values(status=RefundStatus.REFUNDED)
            )
            if affected_rows(moved) == 0:
                raise RuntimeError(f"возврат {row.id} изменился во время запроса к платёжке")
            await self._events.record(
                session,
                EventKind.REFUND_COMPLETED,
                client_id=row.client_id,
                order_id=row.order_id,
                details={REQUEST_DETAIL: row.id, "refund_id": refund_id, "amount": str(amount)},
            )
            await session.commit()
        logger.info("Возврат %s: платёжка вернула %s", row.id, amount)

    async def _fail(self, row: RefundRequest, error: str) -> None:
        """Платёжка отказала: деньги не вернулись — заказ снова «доступ выдан», тревога."""
        logger.error("Возврат %s: платёжка отказала: %s", row.id, error)
        async with self._session_factory() as session:
            await session.execute(
                update(RefundRequest)
                .where(RefundRequest.id == row.id, RefundRequest.status.in_(AWAITING_PROVIDER))
                .values(status=RefundStatus.FAILED)
            )
            await session.execute(
                update(Order)
                .where(Order.id == row.order_id, Order.status == OrderStatus.REFUNDED)
                .values(status=OrderStatus.ACCESS_GRANTED)
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

    async def _shorten(self, row: RefundRequest) -> tuple[RefundProgress, datetime | None]:
        """Снять дни оплаты. Сбой панели — тревога, повтор фоновой задачей."""
        async with self._session_factory() as session:
            client = await session.get_one(Client, row.client_id)
            order = await session.get_one(Order, row.order_id)
        try:
            async with self._term_locks.hold(client.id):
                after = await self._apply_term(row, client, _duration(order))
        except RemnawaveError as error:
            logger.warning("Возврат %s: срок не укорочен: %s", row.id, error)
            await self._alert("refund_term_pending", row, error=f"{type(error).__name__}: {error}")
            return RefundProgress.TERM_PENDING, None
        async with self._session_factory() as session:
            await session.execute(
                update(RefundRequest)
                .where(RefundRequest.id == row.id, RefundRequest.status == RefundStatus.REFUNDED)
                .values(status=RefundStatus.COMPLETED)
            )
            await session.commit()
        logger.info("Возврат %s закончен", row.id)
        await self._messages.refund_completed(
            client.telegram_id, row.amount, after, stars=await self._stars(row.order_id)
        )
        return RefundProgress.COMPLETED, after

    async def _apply_term(
        self, row: RefundRequest, client: Client, duration: timedelta
    ) -> datetime | None:
        """Под блокировкой срока: свежий аккаунт, срок «до X» из журнала или новый расчёт.

        Возвращает новый срок; `None` — доступ выключен (или аккаунта нет).
        """
        try:
            account = await self._remnawave.get_account(client.telegram_id)
        except AccountNotFound:
            # аккаунта нет — снимать нечего, доступа и так нет
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
            term = term_after_refund(account.expire_at, duration, now)
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

    # --- Сообщения администраторам ---

    async def _report(self, request_id: int, progress: RefundProgress) -> None:
        """Сообщение в «💰 Оплаты»: кто вернул, сколько и докуда дошёл возврат."""
        texts = self._admin_texts
        async with self._session_factory() as session:
            row = await session.get_one(RefundRequest, request_id)
            client = await session.get_one(Client, row.client_id)
            admin_name = await _admin_name(session, row)
        if row.decision_reason == RefundKind.DOUBLE_PAYMENT.value:
            who = texts.get("refunds.topic.by_client")
        else:
            who = texts.get("refunds.topic.by_admin", admin=admin_name or row.decided_by_admin_id)
        header = texts.get(
            "refunds.topic.header",
            request_id=row.id,
            order_id=row.order_id,
            amount=paid_text(texts, row.amount, await self._stars(row.order_id)),
            **_client_values(texts, client),
        )
        status = texts.get(f"refunds.topic.{progress.value}")
        await self._payments_topic.post("\n".join([header, who, status]))

    async def _alert(self, kind: str, row: RefundRequest, *, error: str) -> None:
        amount = paid_text(self._admin_texts, row.amount, await self._stars(row.order_id))
        await self._alerter.raise_alert(
            f"{kind}:{row.id}",
            self._admin_texts.get(
                f"alerts.{kind}",
                request_id=row.id,
                order_id=row.order_id,
                amount=amount,
                error=error,
            ),
        )

    async def _row(self, request_id: int) -> RefundRequest:
        async with self._session_factory() as session:
            return await session.get_one(RefundRequest, request_id)

    async def _stars(self, order_id: int) -> int | None:
        async with self._session_factory() as session:
            return await _order_stars(session, order_id)


def refund_idempotency_key(request_id: int) -> str:
    """Ключ идемпотентности возврата у платёжки — номер возврата: повтор не вернёт дважды."""
    return f"refund-request-{request_id}"


async def _order_stars(session: AsyncSession, order_id: int) -> int | None:
    """Сколько звёзд заплачено за заказ; `None` — платили рублями (задача 33)."""
    return (await paid_stars(session, [order_id])).get(order_id)


def _paid_amount(order: Order) -> Decimal:
    if order.paid_amount is None:
        raise ValueError(f"заказ {order.id} без оплаченной суммы")
    return order.paid_amount


def _duration(order: Order) -> timedelta:
    """Сколько срока дала оплата: ровно дни тарифа или пересчёт по сумме (задача 04 п. 8)."""
    return paid_duration(order.days, order.price, _paid_amount(order))


def _client_values(texts: Texts, client: Client) -> dict[str, object]:
    no_value = texts.get("admin.no_value")
    return {
        "name": client.name or no_value,
        "username": client.username or no_value,
        "telegram_id": client.telegram_id,
    }


async def _client(session: AsyncSession, telegram_id: int) -> Client | None:
    return await session.scalar(select(Client).where(Client.telegram_id == telegram_id))


async def _client_order(session: AsyncSession, client_id: int, order_id: int) -> Order | None:
    """Заказ этого клиента — чужой номер из кнопки ничего не даст."""
    return await session.scalar(
        select(Order).where(Order.id == order_id, Order.client_id == client_id)
    )


async def _last_refund_status(session: AsyncSession, order_id: int) -> RefundStatus | None:
    return await session.scalar(
        select(RefundRequest.status)
        .where(RefundRequest.order_id == order_id)
        .order_by(RefundRequest.id.desc())
        .limit(1)
    )


async def _double_payment_refusal(session: AsyncSession, order: Order) -> RefundRefusal | None:
    """Клиент сам может вернуть только отмеченную вторую оплату и только пока не оставил её."""
    details = await session.scalar(
        select(Event.details).where(
            Event.order_id == order.id, Event.kind == EventKind.PAYMENT_RECEIVED.value
        )
    )
    if details is None or details.get(DOUBLE_DETAIL) is None:
        return RefundRefusal.NOT_DOUBLE_PAYMENT
    kept = await session.scalar(
        select(
            exists().where(
                Event.order_id == order.id, Event.kind == EventKind.DOUBLE_PAYMENT_KEPT.value
            )
        )
    )
    return RefundRefusal.KEPT if kept else None


async def _disputed(session: AsyncSession, order_id: int) -> bool:
    """Оплату оспорили через банк (задача 15): вернуть её ещё раз — снять дни дважды."""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    Event.order_id == order_id,
                    Event.kind == EventKind.PAYMENT_CHARGEBACK.value,
                )
            )
        )
    )


async def _transferred_since(session: AsyncSession, client: Client, since: datetime) -> bool:
    """Подписку этого клиента переносили на другой аккаунт после `since`."""
    details = await session.scalars(
        select(Event.details).where(
            Event.client_id == client.id,
            Event.kind == EventKind.ADMIN_ACTION.value,
            Event.occurred_at >= since,
        )
    )
    return any(
        item is not None
        and item.get("action") == AdminActionKind.SUBSCRIPTION_TRANSFERRED.value
        and item.get("from_telegram_id") == client.telegram_id
        for item in details
    )


async def _admin_name(session: AsyncSession, row: RefundRequest) -> str | None:
    details = await _request_event_details(session, row, EventKind.REFUND_APPROVED)
    return None if details is None else details.get("admin_name")


async def _request_event_details(
    session: AsyncSession, row: RefundRequest, kind: EventKind
) -> dict[str, Any] | None:
    """Подробности первого события вида `kind` этого возврата."""
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
