"""Поддельная платёжка для разработки (`PAYMENTS_MODE=fake`) и тестов.

Ведёт себя как настоящая: платёж с ключом идемпотентности, ссылка живёт час и потом
платёж отменяется, возврат — только оплаченного и не больше оплаченного.

Платежи лежат в «книге» (`FakePaymentLedger`). В тестах — в памяти; при ручной проверке
на Mac — в JSON-файле, который делят бот и скрипт `scripts/simulate_payment.py`:
скрипт «оплачивает» платёж в файле, бот видит это, когда спрашивает статус.

Уведомление поддельной платёжки — JSON как у ЮKassa: `{"event": "payment.succeeded",
"object": {"id": "fake_…"}}` (`notification_body`). Скрипт шлёт его боту после «оплаты».
Событие — `payment.<статус>` (`payment.refunded`, `payment.disputed`): по нему бот решает,
спрашивать ли платёжку о засчитанном заказе (задача 15). `object.refund_id` — возврат продавца.

В боевом режиме (`RUN_MODE=prod`) поддельная платёжка не запускается — это проверяют
настройки (аудит R23).
"""

import asyncio
import fcntl
import json
import os
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from tsproxy.domain.dates import require_aware
from tsproxy.domain.payments import payment_link_expires_at
from tsproxy.integrations.payments_types import (
    RUB,
    CreatedPayment,
    InvalidNotification,
    PaymentError,
    PaymentNotFound,
    PaymentNotice,
    PaymentRejected,
    PaymentRequest,
    PaymentState,
    PaymentStatus,
    Refund,
    RefundOutcome,
)

FAKE_PROVIDER_NAME = "fake"
FAKE_PAYMENT_ID_PREFIX = "fake_"
# Ссылка ведёт на зарезервированный для примеров домен: оплатить по ней нельзя,
# «оплату» делает скрипт `simulate_payment.py`
FAKE_PAYMENT_URL = "https://example.com/tsproxy-fake-payment/{payment_id}"
# События в уведомлениях — как у ЮKassa: `payment.<статус>`
EVENT_PREFIX = "payment."
SUCCEEDED_EVENT = f"{EVENT_PREFIX}{PaymentStatus.SUCCEEDED.value}"
CANCELED_EVENT = f"{EVENT_PREFIX}{PaymentStatus.CANCELED.value}"
# Номер платежа в уведомлении не длиннее, чем помещается в базу (`orders.payment_id`)
MAX_NOTIFIED_PAYMENT_ID = 128


@dataclass(frozen=True)
class FakeRefund:
    refund_id: str
    idempotency_key: str
    amount: Decimal


@dataclass(frozen=True)
class FakePayment:
    payment_id: str
    order_id: int
    amount: Decimal
    description: str
    idempotency_key: str
    status: PaymentStatus
    created_at: datetime
    expires_at: datetime
    paid_at: datetime | None = None
    refunds: tuple[FakeRefund, ...] = field(default=())

    @property
    def refunded(self) -> Decimal:
        return sum((r.amount for r in self.refunds), Decimal(0))

    def state(self) -> PaymentState:
        return PaymentState(
            payment_id=self.payment_id,
            status=self.status,
            amount=self.amount,
            currency=RUB,
            order_id=self.order_id,
            test=True,
            paid_at=self.paid_at,
            refunded=self.refunded,
        )

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["amount"] = str(self.amount)
        data["status"] = self.status.value
        data["created_at"] = self.created_at.isoformat()
        data["expires_at"] = self.expires_at.isoformat()
        data["paid_at"] = None if self.paid_at is None else self.paid_at.isoformat()
        data["refunds"] = [
            {
                "refund_id": r.refund_id,
                "idempotency_key": r.idempotency_key,
                "amount": str(r.amount),
            }
            for r in self.refunds
        ]
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "FakePayment":
        paid_at = data["paid_at"]
        return cls(
            payment_id=data["payment_id"],
            order_id=data["order_id"],
            amount=Decimal(data["amount"]),
            description=data["description"],
            idempotency_key=data["idempotency_key"],
            status=PaymentStatus(data["status"]),
            created_at=require_aware(datetime.fromisoformat(data["created_at"])),
            expires_at=require_aware(datetime.fromisoformat(data["expires_at"])),
            paid_at=None if paid_at is None else require_aware(datetime.fromisoformat(paid_at)),
            refunds=tuple(
                FakeRefund(r["refund_id"], r["idempotency_key"], Decimal(r["amount"]))
                for r in data["refunds"]
            ),
        )


type Payments = dict[str, FakePayment]


class FakePaymentLedger:
    """Книга поддельных платежей: в памяти (`path=None`) или в JSON-файле.

    Методы синхронные: файл маленький, а бот зовёт их через `asyncio.to_thread`.
    Файл меняется под блокировкой (`flock`), поэтому бот и скрипт не затрут записи друг друга.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._memory: Payments = {}
        self._lock = threading.Lock()

    # --- Платежи ---

    def create(self, request: PaymentRequest, now: datetime) -> FakePayment:
        """Новый платёж; тот же ключ идемпотентности — тот же платёж, как у настоящей платёжки."""
        with self._payments() as payments:
            for payment in payments.values():
                if payment.idempotency_key != request.idempotency_key:
                    continue
                if (payment.order_id, payment.amount) != (request.order_id, request.amount):
                    raise PaymentRejected("ключ идемпотентности уже занят другим платежом")
                return payment
            payment_id = f"{FAKE_PAYMENT_ID_PREFIX}{uuid4().hex}"
            payment = FakePayment(
                payment_id=payment_id,
                order_id=request.order_id,
                amount=request.amount,
                description=request.description,
                idempotency_key=request.idempotency_key,
                status=PaymentStatus.PENDING,
                created_at=now,
                expires_at=payment_link_expires_at(now, None),
            )
            payments[payment_id] = payment
            return payment

    def get(self, payment_id: str, now: datetime) -> FakePayment:
        """Платёж; неоплаченный и просроченный — уже отменён."""
        with self._payments() as payments:
            return self._expire(payments, self._find(payments, payment_id), now)

    def pay(self, payment_id: str, now: datetime, amount: Decimal | None = None) -> FakePayment:
        """«Клиент оплатил». `amount` — оплачено не на сумму тарифа (сбой платёжки)."""
        with self._payments() as payments:
            payment = self._expire(payments, self._find(payments, payment_id), now)
            if payment.status is not PaymentStatus.PENDING:
                raise PaymentRejected(f"платёж {payment_id} уже {payment.status.value}")
            if amount is not None and amount <= 0:
                raise PaymentRejected("оплаченная сумма должна быть больше нуля")
            paid = replace(
                payment,
                status=PaymentStatus.SUCCEEDED,
                paid_at=now,
                amount=payment.amount if amount is None else amount,
            )
            payments[payment_id] = paid
            return paid

    def cancel(self, payment_id: str) -> FakePayment:
        """«Клиент не стал платить» — платёжка отменила платёж."""
        with self._payments() as payments:
            payment = self._find(payments, payment_id)
            if payment.status is not PaymentStatus.PENDING:
                raise PaymentRejected(f"платёж {payment_id} уже {payment.status.value}")
            payments[payment_id] = canceled = replace(payment, status=PaymentStatus.CANCELED)
            return canceled

    def change_status(self, payment_id: str, status: PaymentStatus) -> FakePayment:
        """«Платёжка сменила статус оплаченного платежа»: спор, чарджбэк (задача 15)."""
        with self._payments() as payments:
            payment = self._find(payments, payment_id)
            payments[payment_id] = changed = replace(payment, status=status)
            return changed

    def refund(self, payment_id: str, amount: Decimal, idempotency_key: str) -> FakeRefund:
        """Вернуть часть или всю оплату. Тот же ключ — тот же возврат."""
        with self._payments() as payments:
            payment = self._find(payments, payment_id)
            for done in payment.refunds:
                if done.idempotency_key == idempotency_key:
                    return done
            if payment.status is not PaymentStatus.SUCCEEDED:
                raise PaymentRejected(f"платёж {payment_id} не оплачен — возвращать нечего")
            if amount <= 0 or payment.refunded + amount > payment.amount:
                raise PaymentRejected(f"нельзя вернуть {amount}: оплачено {payment.amount}")
            refund = FakeRefund(
                f"{FAKE_PAYMENT_ID_PREFIX}refund_{uuid4().hex}", idempotency_key, amount
            )
            payments[payment_id] = replace(payment, refunds=(*payment.refunds, refund))
            return refund

    # --- Для скрипта и тестов ---

    def all(self) -> list[FakePayment]:
        with self._payments() as payments:
            return sorted(payments.values(), key=lambda p: p.created_at)

    def find_by_order(self, order_id: int) -> list[FakePayment]:
        return [p for p in self.all() if p.order_id == order_id]

    def created_between(self, since: datetime, until: datetime, now: datetime) -> list[FakePayment]:
        """Платежи, созданные в `[since, until)`; просроченные неоплаченные — уже отменены."""
        with self._payments() as payments:
            found = [p for p in payments.values() if since <= p.created_at < until]
            return [self._expire(payments, p, now) for p in sorted(found, key=_created)]

    # --- Хранение ---

    @staticmethod
    def _find(payments: Payments, payment_id: str) -> FakePayment:
        try:
            return payments[payment_id]
        except KeyError:
            raise PaymentNotFound(f"платёж {payment_id} не найден") from None

    @staticmethod
    def _expire(payments: Payments, payment: FakePayment, now: datetime) -> FakePayment:
        if payment.status is PaymentStatus.PENDING and require_aware(now) >= payment.expires_at:
            payment = payments[payment.payment_id] = replace(payment, status=PaymentStatus.CANCELED)
        return payment

    @contextmanager
    def _payments(self) -> Iterator[Payments]:
        """Платежи для чтения и изменения; изменения сохраняются, если не было ошибки."""
        with self._lock:
            if self._path is None:
                snapshot = dict(self._memory)
                yield snapshot
                self._memory = snapshot
                return
            path = self._path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.with_suffix(".lock").open("a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                payments = _read_file(path)
                yield payments
                _write_file(path, payments)


def _created(payment: FakePayment) -> datetime:
    return payment.created_at


def _read_file(path: Path) -> Payments:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {pid: FakePayment.from_json(data) for pid, data in raw["payments"].items()}


def _write_file(path: Path, payments: Payments) -> None:
    data = {"payments": {pid: p.to_json() for pid, p in payments.items()}}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)  # целиком или никак — файл не останется недописанным


class _NotificationObject(BaseModel):
    id: str = Field(min_length=1, max_length=MAX_NOTIFIED_PAYMENT_ID)
    refund_id: str | None = Field(default=None, max_length=MAX_NOTIFIED_PAYMENT_ID)


class _Notification(BaseModel):
    """Уведомление поддельной платёжки. Из тела берём только номер платежа."""

    event: str
    object: _NotificationObject


def notification_body(
    payment_id: str, event: str = SUCCEEDED_EVENT, *, refund_id: str | None = None
) -> bytes:
    """Тело уведомления поддельной платёжки — для скрипта и тестов."""
    payment: dict[str, str] = {"id": payment_id}
    if refund_id is not None:
        payment["refund_id"] = refund_id
    return json.dumps({"event": event, "object": payment}).encode()


def _event_status(event: str) -> PaymentStatus | None:
    """`payment.refunded` → `REFUNDED`; незнакомое событие — без подсказки."""
    if not event.startswith(EVENT_PREFIX):
        return None
    try:
        return PaymentStatus(event.removeprefix(EVENT_PREFIX))
    except ValueError:
        return None


class FakePaymentProvider:
    """Поддельная платёжка за интерфейсом `PaymentProvider`.

    Для тестов: `fail_next(PaymentUnavailable(...))` — следующий запрос упадёт этой ошибкой;
    `ledger.pay(...)` / `ledger.cancel(...)` — «клиент оплатил» / «не стал платить»;
    `review_next_refund()` — следующий возврат уйдёт «на проверку» (как `manualControlRequired`
    у Platega, задача 21).
    """

    def __init__(
        self, clock: Callable[[], datetime], ledger: FakePaymentLedger | None = None
    ) -> None:
        self._clock = clock
        self.ledger = ledger if ledger is not None else FakePaymentLedger()
        self._next_error: PaymentError | None = None
        self._review_next_refund = False

    @property
    def name(self) -> str:
        return FAKE_PROVIDER_NAME

    def fail_next(self, error: PaymentError) -> None:
        self._next_error = error

    def review_next_refund(self) -> None:
        self._review_next_refund = True

    async def create_payment(self, request: PaymentRequest) -> CreatedPayment:
        payment = await self._call(self.ledger.create, request, require_aware(self._clock()))
        return CreatedPayment(
            payment_id=payment.payment_id,
            url=FAKE_PAYMENT_URL.format(payment_id=payment.payment_id),
            expires_at=payment.expires_at,
        )

    async def get_payment(self, payment_id: str) -> PaymentState:
        payment = await self._call(self.ledger.get, payment_id, require_aware(self._clock()))
        return payment.state()

    async def refund(self, payment_id: str, amount: Decimal, idempotency_key: str) -> Refund:
        done = await self._call(self.ledger.refund, payment_id, amount, idempotency_key)
        outcome = RefundOutcome.IN_REVIEW if self._review_next_refund else RefundOutcome.SUCCEEDED
        self._review_next_refund = False
        return Refund(done.refund_id, payment_id, done.amount, outcome)

    def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> PaymentNotice:
        """Подписи у поддельной платёжки нет, как у ЮKassa: правду бот узнаёт перезапросом."""
        try:
            notification = _Notification.model_validate_json(body)
        except ValidationError as error:
            raise InvalidNotification(
                f"уведомление не разобрано: {error.error_count()} ошибок"
            ) from None
        return PaymentNotice(
            notification.object.id,
            _event_status(notification.event),
            notification.object.refund_id,
        )

    async def list_payments(self, since: datetime, until: datetime) -> Sequence[PaymentState]:
        now = require_aware(self._clock())
        found = await self._call(
            self.ledger.created_between, require_aware(since), require_aware(until), now
        )
        return [payment.state() for payment in found]

    async def _call[**P, R](
        self, operation: Callable[P, R], *args: P.args, **kwargs: P.kwargs
    ) -> R:
        if self._next_error is not None:
            error, self._next_error = self._next_error, None
            raise error
        return await asyncio.to_thread(operation, *args, **kwargs)
