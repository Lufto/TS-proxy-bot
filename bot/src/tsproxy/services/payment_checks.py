"""Проверка оплат: уведомления платёжки, «🔄 Проверить оплату», фоновая проверка и «Купить»
(задача 14; задачи 04 пп. 4–5, 21 п. 12; аудит §4.1; карта оплаты, уязвимости 1, 2, 11).

Все пути сходятся в одно правило: **уведомлению не верим, спрашиваем платёжку**
(`get_payment`) и засчитываем через `CreditingService.credit` — ровно один раз.

- **Уведомление** — от кого угодно, без фильтра по IP. Из тела берём только номер платежа
  (разбирает сама платёжка, `PaymentProvider.parse_notification`). Нет заказа с таким
  платежом — подделка: одна склеенная запись в журнале и одна тревога, к платёжке не идём
  (иначе флуд выдуманными номерами стал бы флудом запросов от нашего имени). Заказ уже
  засчитан — повтор, тоже без запроса. Иначе — перепроверка у платёжки.
- **«Создан» и «отменён» — оба ждут оплаты.** Отменённый (ссылка истекла) тоже
  перепроверяется и засчитывается: подтверждённая оплата засчитывается всегда (задача 04 п. 4,
  решение владельца 2026-10-25 п. 2 — и через сутки, и позже).
- **Перепроверка сверяет всё**: статус, номер платежа и заказа, рубли, сумму (это делает
  зачёт) и признак «тестовый» — он должен совпасть с режимом (`test_payments`).
  Платёжка говорит «оплачено», а что-то не сходится — не засчитываем, тревога.
- **«Купить»** (`find_paid`): до новой ссылки спросить платёжку о незасчитанных заказах
  клиента за сутки (не больше трёх свежих). Нашлась оплата — засчитать и ссылку не давать.
  Платёжка не ответила — ссылку тоже не давать (`PaymentUnavailable` летит дальше).
- **Фоновая проверка** в два темпа: заказы «создан» младше 2 часов — часто (`check_fresh`),
  от 2 до 72 часов вместе с «отменён» — реже (`check_stale`). Платёж отменён у платёжки —
  заказ «отменён» (его оплата всё ещё засчитается, если придёт).

Задача 15 (Platega):
- **Статус изменился после оплаты** (спор через банк, возврат, проверка). Уведомление
  по засчитанному заказу о статусе «оплачен» — повтор, без запроса. О другом статусе
  (`PaymentNotice.status`) — перепроверка у платёжки, и решает `PaymentStatusChanges`.
  Подсказка в уведомлении решает только «спросить или нет»: подлинность колбэка Platega
  проверена по секрету, а что делать — по ответу платёжки. Неожиданный статус у заказа,
  который ещё ждёт оплаты (спор, возврат), — тоже туда: тревога, не зачёт.
- **Возвраты «на проверке у платёжки»** (`check_refunds`): итог Platega шлёт колбэком
  `CHARGEBACKED`, а об отказе в возврате всей суммы не сообщает вовсе. Раз в 10 минут бот
  спрашивает платёжку сам: деньги вернулись — снимает дни; больше суток без итога — одна
  тревога «проверьте возврат в кабинете».
- **Платёжка отказала в проверке** (неверные ключи и т. п.) — одна склеенная тревога,
  заказ остаётся ждать: проверим снова, когда она ответит.

Оплата выключена (`PAYMENTS_MODE=off`) — платёжки нет, проверять не у кого: фоновые
проверки ничего не делают, адрес уведомлений не подключается (`tsproxy.app`).

Замки: `find_paid` вызывается из `OrderService.checkout` под замком заказов клиента,
а зачёт внутри берёт замок срока (`term_locks`). Порядок всегда «заказы → срок»: зачёт,
выдача и фоновые задачи замок заказов не берут, поэтому взаимной блокировки нет.
"""

import logging
from collections.abc import Sequence
from enum import StrEnum

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.amounts import paid_text
from tsproxy.content.loader import Texts
from tsproxy.db.enums import OrderStatus, RefundStatus
from tsproxy.db.models import Event, Order, RefundRequest
from tsproxy.db.repository import CREDITABLE_ORDER_STATUSES
from tsproxy.domain.constants import (
    FRESH_ORDER_AGE,
    ONE_HOUR,
    RECENT_PAYMENTS_LIMIT,
    RECENT_PAYMENTS_WINDOW,
    REFUND_REVIEW_OVERDUE,
    STALE_ORDER_AGE,
)
from tsproxy.integrations.payments_types import (
    PaymentNotFound,
    PaymentNotice,
    PaymentProvider,
    PaymentRejected,
    PaymentState,
    PaymentStatus,
    PaymentUnavailable,
)
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.crediting import CreditingService, PaymentMismatch, PaymentOutcome
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.orders import PaymentsDisabled
from tsproxy.services.payment_status import PaymentStatusChanges

logger = logging.getLogger(__name__)

# Сколько заказов фоновая проверка берёт за один проход
CHECK_BATCH_SIZE = 100
# Подделки склеиваются в одну запись журнала и одну тревогу (решение по аудиту, п. 13)
FORGED_NOTIFICATION_GLUE_KEY = "forged_payment_notification"
FORGED_NOTIFICATION_ALERT = "payment_notification_forged"
# Фоновая проверка не достучалась до платёжки — одна склеенная тревога
PAYMENT_CHECKS_UNAVAILABLE_ALERT = "payment_checks_unavailable"
# Сколько символов присланного номера платежа сохранять в журнал подделок
FORGED_ID_DETAIL_LENGTH = 64
# Платёжка отказала ответить на проверку (неверные ключи) — одна склеенная тревога (задача 15)
PAYMENT_CHECK_REFUSED_ALERT = "payment_check_refused"
# Платёж не сошёлся с заказом — тревога на каждый заказ
PAYMENT_REJECTED_ALERT = "payment_rejected"
REFUND_OVERDUE_ALERT = "refund_review_overdue"
# Статусы, которые обычная проверка разбирает сама; остальные — `PaymentStatusChanges`
ORDINARY_STATUSES = (PaymentStatus.SUCCEEDED, PaymentStatus.CANCELED, PaymentStatus.PENDING)
# Уведомление с таким статусом по засчитанному заказу — повтор, к платёжке не идём
REPEAT_HINTS = (None, PaymentStatus.SUCCEEDED)


class CheckOutcome(StrEnum):
    """Что показала проверка оплаты заказа."""

    CREDITED = "credited"  # оплата найдена и засчитана сейчас
    ALREADY_CREDITED = "already_credited"  # заказ был засчитан раньше
    NOT_PAID = "not_paid"  # платёж ещё ждёт оплаты
    CANCELED = "canceled"  # платёж отменён или ссылка истекла, оплаты нет
    NOT_FOUND = "not_found"  # нет такого заказа (у этого клиента) или платежа
    REJECTED = "rejected"  # платёжка говорит «оплачено», но платёж не сходится — тревога
    # задача 15: статус изменился после оплаты (спор, возврат) — разобрано, у засчитанного
    # заказа — без зачёта; у незасчитанного — тревога
    STATUS_CHANGED = "status_changed"


class ForgedReason(StrEnum):
    """Почему уведомление считаем подделкой."""

    UNPARSED = "unparsed"  # тело не разобрано или подпись неверна
    UNKNOWN_PAYMENT = "unknown_payment"  # заказа с таким платежом у нас нет


class PaymentChecks:
    """Спросить платёжку о платеже заказа и засчитать оплату, если она есть."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        payments: PaymentProvider | None,  # None — оплата выключена (задача 29)
        crediting: CreditingService,
        events: EventLog,
        alerter: Alerter,
        admin_texts: Texts,
        clock: Clock,
        *,
        test_payments: bool,  # ждём тестовые платежи (разработка) или настоящие (бой)
        status_changes: PaymentStatusChanges,  # задача 15: статус изменился после оплаты
    ) -> None:
        self._session_factory = session_factory
        self._payments = payments
        self._crediting = crediting
        self._events = events
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._clock = clock
        self._test_payments = test_payments
        self._status_changes = status_changes

    # --- Уведомление платёжки ---

    async def handle_notification(self, notice: PaymentNotice, source: str | None) -> CheckOutcome:
        """Уведомление о платеже. `source` — адрес отправителя, только для журнала.

        Платёжка не ответила на перепроверку — `PaymentUnavailable`: ответить ей ошибкой,
        она пришлёт уведомление ещё раз (а фоновая проверка найдёт оплату и без него).
        """
        payments = self._require_payments()
        order = await self._order_by_payment(payments.name, notice.payment_id)
        if order is None:
            await self.record_forged(ForgedReason.UNKNOWN_PAYMENT, notice.payment_id, source)
            return CheckOutcome.NOT_FOUND
        if order.status in CREDITABLE_ORDER_STATUSES:
            return await self.settle(order, notice)
        if notice.status in REPEAT_HINTS:
            logger.info("Уведомление по заказу %s: уже засчитан — повтор", order.id)
            return CheckOutcome.ALREADY_CREDITED
        return await self._recheck_credited(order, notice)

    async def _recheck_credited(self, order: Order, notice: PaymentNotice) -> CheckOutcome:
        """Засчитанный заказ, а платёжка сообщает другой статус: спросить её и разобрать."""
        state = await self._ask(order)
        if state is None:
            return CheckOutcome.NOT_FOUND
        change = await self._status_changes.changed(order, state, notice)
        logger.info("Заказ %s: статус оплаты %s — %s", order.id, state.status_name, change.value)
        return CheckOutcome.STATUS_CHANGED

    async def record_forged(
        self, reason: ForgedReason, payment_id: str | None, source: str | None
    ) -> None:
        """Подделка: одна склеенная запись в журнале и одна (склеенная) тревога."""
        logger.warning("Поддельное уведомление об оплате (%s) от %s", reason.value, source)
        async with self._session_factory() as session:
            await self._events.record(
                session,
                EventKind.FAKE_PAYMENT_NOTIFICATION,
                details={
                    "reason": reason.value,
                    "payment_id": None
                    if payment_id is None
                    else payment_id[:FORGED_ID_DETAIL_LENGTH],
                    "source": source,  # IP — только справка, по нему не отбрасываем
                },
                glue_key=FORGED_NOTIFICATION_GLUE_KEY,
            )
            await session.commit()
        await self._alerter.alert(
            FORGED_NOTIFICATION_ALERT,
            source=self.record_forged,
            reason=self._admin_texts.get(f"forged_reason.{reason.value}"),
        )

    # --- «🔄 Проверить оплату» ---

    async def check_order(self, client_id: int, order_id: int) -> CheckOutcome:
        """Кнопка «Проверить оплату»: заказ ищем только среди заказов нажавшего (аудит R07).

        Платёжка не ответила — `PaymentUnavailable`.
        """
        payments = self._require_payments()
        async with self._session_factory() as session:
            order = await session.get(Order, order_id)
        if order is None or order.client_id != client_id or order.payment_id is None:
            logger.info("Клиент %s проверяет чужой или пустой заказ %s", client_id, order_id)
            return CheckOutcome.NOT_FOUND
        if order.status not in CREDITABLE_ORDER_STATUSES:
            return CheckOutcome.ALREADY_CREDITED
        if order.payment_provider != payments.name:
            # заказ другой платёжки (её сменили) — спросить не у кого, задача 15
            logger.warning("Заказ %s платёжки %s не проверить", order.id, order.payment_provider)
            return CheckOutcome.NOT_FOUND
        return await self.settle(order)

    # --- «Купить»: сначала прошлые оплаты (уязвимость 1) ---

    async def find_paid(self, client_id: int) -> int | None:
        """Засчитать оплату по прошлым заказам клиента за сутки, если она есть.

        Возвращает номер заказа, оплата которого нашлась (засчитана сейчас или только что
        другим путём), — тогда новую ссылку давать нельзя. Платёжка не ответила —
        `PaymentUnavailable`: новую ссылку тоже не давать.
        """
        payments = self._payments
        if payments is None:
            return None
        now = utc_now(self._clock)
        orders = await self._orders(
            _awaiting(payments.name)
            .where(
                Order.client_id == client_id,
                Order.status.in_(CREDITABLE_ORDER_STATUSES),
                Order.created_at > now - RECENT_PAYMENTS_WINDOW,
            )
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(RECENT_PAYMENTS_LIMIT)
        )
        for order in orders:
            outcome = await self.settle(order)
            if outcome in (CheckOutcome.CREDITED, CheckOutcome.ALREADY_CREDITED):
                logger.info(
                    "Клиент %s: нашлась оплата заказа %s — новой ссылки нет", client_id, order.id
                )
                return order.id
        return None

    # --- Фоновая проверка в два темпа ---

    async def check_fresh(self) -> None:
        """Заказы «создан» младше 2 часов: потерянное уведомление, бот лежал (раз в 2 минуты)."""
        payments = self._payments
        if payments is None:
            return  # оплата выключена — проверять не у кого
        now = utc_now(self._clock)
        await self._check_all(
            _awaiting(payments.name).where(
                Order.status == OrderStatus.CREATED,
                Order.created_at > now - FRESH_ORDER_AGE,
            )
        )

    async def check_stale(self) -> None:
        """Заказы от 2 до 72 часов, включая отменённые: поздние оплаты (раз в 30 минут)."""
        payments = self._payments
        if payments is None:
            return
        now = utc_now(self._clock)
        await self._check_all(
            _awaiting(payments.name).where(
                Order.status.in_(CREDITABLE_ORDER_STATUSES),
                Order.created_at <= now - FRESH_ORDER_AGE,
                Order.created_at > now - STALE_ORDER_AGE,
            )
        )

    async def _check_all(self, query: Select[tuple[Order]]) -> None:
        orders = await self._orders(query.order_by(Order.created_at).limit(CHECK_BATCH_SIZE))
        if len(orders) == CHECK_BATCH_SIZE:
            logger.warning(
                "Проверка оплат: заказов больше %d, остальные — в следующий раз", CHECK_BATCH_SIZE
            )
        for order in orders:
            try:
                await self.settle(order)
            except PaymentUnavailable as error:
                # платёжка лежит — остальные заказы спросим в следующий проход
                logger.warning("Проверка оплат: платёжка не ответила: %s", error)
                await self._alerter.alert(
                    PAYMENT_CHECKS_UNAVAILABLE_ALERT, source=self._check_all, error=error
                )
                return

    # --- Общая перепроверка ---

    async def settle(self, order: Order, notice: PaymentNotice | None = None) -> CheckOutcome:
        """Спросить платёжку о платеже заказа и поступить по ответу.

        Платёжка не ответила — `PaymentUnavailable`.
        """
        state = await self._ask(order)
        if state is None:
            return CheckOutcome.NOT_FOUND
        if state.status not in ORDINARY_STATUSES:
            # спор, возврат или незнакомый статус у заказа, который мы не засчитывали
            await self._status_changes.changed(order, state, notice)
            return CheckOutcome.STATUS_CHANGED
        if state.status is PaymentStatus.SUCCEEDED:
            return await self._credit(order, state)
        if state.status is PaymentStatus.CANCELED:
            await self._mark_canceled(order.id)
            return CheckOutcome.CANCELED
        return CheckOutcome.NOT_PAID

    async def _ask(self, order: Order) -> PaymentState | None:
        """Платёж заказа у платёжки. `None` — платёжка его не знает или отказала (тревога).

        Платёжка не ответила — `PaymentUnavailable`.
        """
        payments = self._require_payments()
        if order.payment_id is None:
            raise ValueError(f"заказ {order.id} без платежа")
        try:
            return await payments.get_payment(order.payment_id)
        except PaymentNotFound:
            logger.error("Платёжка не знает платёж %s заказа %s", order.payment_id, order.id)
            await self._alert_rejected(order, order.payment_id, "платёжка не знает такого платежа")
        except PaymentRejected as error:
            logger.error("Платёжка отказала в проверке заказа %s: %s", order.id, error)
            await self._alerter.alert(
                PAYMENT_CHECK_REFUSED_ALERT,
                source=self._ask,
                error=error,
                order_id=order.id,
            )
        return None

    # --- Возвраты «на проверке у платёжки» (задача 15) ---

    async def check_refunds(self) -> None:
        """Раз в 10 минут: спросить платёжку о возвратах, которые ждут её итога."""
        payments = self._payments
        if payments is None:
            return  # оплата выключена — спросить не у кого
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(RefundRequest, Order)
                    .join(Order, Order.id == RefundRequest.order_id)
                    .where(
                        RefundRequest.status == RefundStatus.IN_REVIEW,
                        Order.payment_provider == payments.name,
                        Order.payment_id.is_not(None),
                    )
                    .order_by(RefundRequest.id)
                    .limit(CHECK_BATCH_SIZE)
                )
            ).all()
        for refund, order in rows:
            try:
                state = await self._ask(order)
            except PaymentUnavailable as error:
                logger.warning("Проверка возвратов: платёжка не ответила: %s", error)
                return
            if state is not None and state.status is PaymentStatus.REFUNDED:
                await self._status_changes.changed(order, state, None)
            else:
                await self._alert_overdue(refund)

    async def _alert_overdue(self, refund: RefundRequest) -> None:
        """Возврат дольше суток без итога — одна тревога (Platega об отказе может промолчать)."""
        waited = utc_now(self._clock) - refund.requested_at
        if waited < REFUND_REVIEW_OVERDUE:
            return
        async with self._session_factory() as session:
            alerted = await session.scalar(
                select(Event.id).where(
                    Event.order_id == refund.order_id,
                    Event.kind == EventKind.REFUND_REVIEW_OVERDUE.value,
                )
            )
            if alerted is not None:
                return
            await self._events.record(
                session,
                EventKind.REFUND_REVIEW_OVERDUE,
                client_id=refund.client_id,
                order_id=refund.order_id,
                details={"request_id": refund.id},
            )
            await session.commit()
        logger.warning("Возврат %s: больше суток на проверке у платёжки", refund.id)
        await self._alerter.alert(
            REFUND_OVERDUE_ALERT,
            source=self.check_refunds,
            glue=f"{REFUND_OVERDUE_ALERT}:{refund.id}",
            request_id=refund.id,
            order_id=refund.order_id,
            amount=paid_text(self._admin_texts, refund.amount, None),
            hours=waited // ONE_HOUR,
        )

    async def _credit(self, order: Order, state: PaymentState) -> CheckOutcome:
        if state.test is not self._test_payments:
            # тестовый платёж в бою или настоящий на тестовом стенде (аудит R08, уязвимость 11)
            await self._alert_rejected(
                order, state.payment_id, f"признак «тестовый» = {state.test}"
            )
            return CheckOutcome.REJECTED
        try:
            result = await self._crediting.credit(order.id, state)
        except PaymentMismatch as error:
            await self._alert_rejected(order, state.payment_id, str(error))
            return CheckOutcome.REJECTED
        if result.outcome is PaymentOutcome.ALREADY_CREDITED:
            return CheckOutcome.ALREADY_CREDITED
        return CheckOutcome.CREDITED

    async def _alert_rejected(self, order: Order, payment_id: str, reason: str) -> None:
        logger.error("Заказ %s: платёж %s не засчитан: %s", order.id, payment_id, reason)
        await self._alerter.alert(
            PAYMENT_REJECTED_ALERT,
            source=self._alert_rejected,
            glue=f"{PAYMENT_REJECTED_ALERT}:{order.id}",
            order_id=order.id,
            payment_id=payment_id,
            reason=reason,
        )

    async def _mark_canceled(self, order_id: int) -> None:
        """Платёж отменён у платёжки — заказ «отменён». Оплата по нему всё ещё засчитается."""
        async with self._session_factory() as session:
            await session.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == OrderStatus.CREATED)
                .values(status=OrderStatus.CANCELED)
            )
            await session.commit()
        logger.info("Заказ %s: платёж отменён у платёжки", order_id)

    # --- База ---

    async def _order_by_payment(self, provider: str, payment_id: str) -> Order | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(Order).where(
                    Order.payment_provider == provider, Order.payment_id == payment_id
                )
            )

    async def _orders(self, query: Select[tuple[Order]]) -> Sequence[Order]:
        async with self._session_factory() as session:
            return list(await session.scalars(query))

    def _require_payments(self) -> PaymentProvider:
        if self._payments is None:
            raise PaymentsDisabled("оплата выключена: проверять оплату не у кого")
        return self._payments


def _awaiting(provider: str) -> Select[tuple[Order]]:
    """Заказы этой платёжки с платежом — их можно спросить у платёжки."""
    return select(Order).where(Order.payment_provider == provider, Order.payment_id.is_not(None))
