"""Общий язык для платёжки: интерфейс «создать платёж / узнать статус / вернуть деньги».

Платёжка ещё не выбрана (спецификация, «Оплата»), поэтому бот знает только этот интерфейс.
Сейчас за ним — поддельная платёжка (`fake_payments.py`, режим `PAYMENTS_MODE=fake`),
настоящую напишем в задаче 15 за этим же интерфейсом.

Правило для всех реализаций: платёжке не доверяем на слово. `get_payment` — источник правды
о платеже, а уведомления платёжки — только повод его спросить (аудит §4.1).

Формат уведомления у каждой платёжки свой, поэтому разбирает его сама реализация
(`parse_notification`): наружу выходит только номер платежа (задача 14).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

RUB = "RUB"


class PaymentStatus(StrEnum):
    """Что с платежом у платёжки."""

    PENDING = "pending"  # ждёт оплаты
    SUCCEEDED = "succeeded"  # деньги получены
    CANCELED = "canceled"  # отменён или истёк — оплатить уже нельзя


class RefundOutcome(StrEnum):
    """Что с возвратом у платёжки."""

    PENDING = "pending"  # принят, ещё обрабатывается — итог придёт позже
    # платёжка отправила возврат на ручную проверку (у Platega — `manualControlRequired`);
    # итог придёт позже уведомлением (задача 15)
    IN_REVIEW = "in_review"
    SUCCEEDED = "succeeded"  # деньги возвращены
    CANCELED = "canceled"  # платёжка отказала


class PaymentError(Exception):
    """Ошибка платёжки."""


class PaymentUnavailable(PaymentError):
    """Платёжка не ответила (сеть, таймаут, 5xx). Повтор с тем же ключом безопасен."""


class PaymentNotFound(PaymentError):
    """Платёжка не знает такого платежа."""


class PaymentRejected(PaymentError):
    """Платёжка отказала: неверный запрос, платёж нельзя оплатить или вернуть."""


class InvalidNotification(PaymentError):
    """Уведомление не разобрано: не тот формат или неверная подпись. Скорее всего, подделка."""


@dataclass(frozen=True)
class PaymentRequest:
    """Что просим у платёжки. Сумма — из заказа (то есть из `tariffs.yaml`), а не из кнопки."""

    order_id: int
    amount: Decimal  # рубли
    description: str  # видит клиент на странице оплаты и в чеке
    idempotency_key: str  # повтор с тем же ключом не создаёт второй платёж


@dataclass(frozen=True)
class CreatedPayment:
    payment_id: str
    url: str  # куда отправить клиента платить
    expires_at: datetime | None  # когда платёжка сама отменит неоплаченный платёж


@dataclass(frozen=True)
class PaymentState:
    """Платёж так, как его видит платёжка. Задача 14 сверяет каждое поле с заказом."""

    payment_id: str
    status: PaymentStatus
    amount: Decimal  # сколько оплачено (или ждёт оплаты)
    currency: str
    order_id: int | None  # номер заказа, который мы передали платёжке
    test: bool  # тестовый платёж (поддельная платёжка, тестовый магазин)
    paid_at: datetime | None
    refunded: Decimal  # сколько уже вернули клиенту по этому платежу (задача 14: сверка)
    # Оплачено звёздами Telegram (задача 33): сколько звёзд. Тогда `amount` — рубли заказа,
    # которые покрывает эта оплата (`domain.stars.paid_rubles`), а `currency` — рубли
    stars: int | None = None


@dataclass(frozen=True)
class Refund:
    refund_id: str
    payment_id: str
    amount: Decimal
    outcome: RefundOutcome


@dataclass(frozen=True)
class PaidPayment:
    """Оплата, которую возвращаем: чья она (`provider` из заказа), номер у платёжки и кто платил.

    Telegram Stars возвращает по паре «клиент + номер платежа» (`refundStarPayment`),
    поэтому возврату нужен и Telegram ID плательщика (задача 33).
    """

    provider: str
    payment_id: str
    telegram_id: int


class PaymentRefunds(Protocol):
    """Возврат оплаты. Реализации: `integrations.refunds.ProviderRefunds` (платёжка),
    `integrations.telegram_stars.TelegramStarsRefunds` (звёзды) и `RefundRouter` над ними.

    Ошибки — как у платёжки: `PaymentUnavailable` — повторить позже, другие `PaymentError` —
    отказ. Тот же `idempotency_key` — тот же возврат.
    """

    async def refund(self, payment: PaidPayment, amount: Decimal, idempotency_key: str) -> Refund:
        """Вернуть `amount` рублей заказа по оплате `payment` (звёзды возвращаются целиком)."""
        ...


class PaymentProvider(Protocol):
    """Платёжка. Реализации: `FakePaymentProvider`, настоящая — задача 15."""

    @property
    def name(self) -> str:
        """Короткое имя платёжки — пишется в заказ (`payment_provider`)."""
        ...

    async def create_payment(self, request: PaymentRequest) -> CreatedPayment:
        """Создать платёж. Тот же `idempotency_key` — тот же платёж."""
        ...

    async def get_payment(self, payment_id: str) -> PaymentState:
        """Спросить у платёжки, что с платежом. Нет такого — `PaymentNotFound`."""
        ...

    async def refund(self, payment_id: str, amount: Decimal, idempotency_key: str) -> Refund:
        """Вернуть клиенту `amount` рублей по оплаченному платежу. Чек аннулирует платёжка.

        Тот же `idempotency_key` — тот же возврат (повтор после сбоя деньги дважды не вернёт).
        Возврат может уйти на проверку (`RefundOutcome.IN_REVIEW`) — итог придёт позже.
        """
        ...

    def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> str:
        """Номер платежа из уведомления платёжки (задача 14). Остальному в теле не верим.

        Не тот формат или неверная подпись — `InvalidNotification`. Сеть не трогает.
        """
        ...

    async def list_payments(self, since: datetime, until: datetime) -> Sequence[PaymentState]:
        """Платежи, созданные с `since` (включительно) до `until` — для ежедневной сверки."""
        ...
