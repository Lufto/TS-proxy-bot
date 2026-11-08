"""Общий язык для платёжки: интерфейс «создать платёж / узнать статус / вернуть деньги».

Бот знает только этот интерфейс. За ним — Platega (`platega.py`, `PAYMENTS_MODE=platega`,
задача 15) или поддельная платёжка для разработки и тестов (`fake_payments.py`,
`PAYMENTS_MODE=fake`). Остальной код по платёжке не ветвится.

Правило для всех реализаций: платёжке не доверяем на слово. `get_payment` — источник правды
о платеже, а уведомления платёжки — только повод его спросить (аудит §4.1).

Формат уведомления у каждой платёжки свой, поэтому разбирает его сама реализация
(`parse_notification`): наружу выходят номер платежа и подсказки (`PaymentNotice`) —
о каком статусе уведомление и был ли это возврат. По подсказкам бот решает только, нужно ли
спросить платёжку о уже засчитанном заказе; что делать — решает ответ `get_payment`.
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
    # Задача 15 (статусы Platega): что бывает с платежом после оплаты
    REFUNDED = "refunded"  # деньги вернули плательщику целиком (`CHARGEBACKED` у Platega)
    PARTIALLY_REFUNDED = "partially_refunded"  # вернули часть — у нас частичных нет
    DISPUTED = "disputed"  # платёж на проверке (спор): деньги зарезервированы до решения
    UNKNOWN = "unknown"  # статус, которого бот не знает, — смотреть `provider_status`


# Статусы, при которых деньги клиента до нас дошли (в том числе если потом их вернули)
PAID_STATUSES = frozenset(
    {
        PaymentStatus.SUCCEEDED,
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.DISPUTED,
    }
)


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
    # Telegram ID плательщика. Платёжке не передаётся, пока не понадобится email клиента
    # (решение владельца 2026-10-31; у Platega — `{telegram_id}@telegram.org`, задача 15)
    telegram_id: int | None = None


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
    # Статус так, как его назвала платёжка (`CHARGEBACKED`) — для тревог; задача 15
    provider_status: str | None = None

    @property
    def status_name(self) -> str:
        """Имя статуса для тревог: как у платёжки, а если она не назвала — наше."""
        return self.provider_status or self.status.value


@dataclass(frozen=True)
class PaymentNotice:
    """Что платёжка сообщила уведомлением (задача 15). Подлинность уже проверена
    (`parse_notification`), но статусу в теле всё равно не верим: это только подсказка,
    спрашивать ли платёжку о заказе, который уже засчитан.

    `status=None` — платёжка статус не сообщает (тогда считаем, что про оплату).
    `refund_id` — уведомление о возврате, который сделал продавец (бот или кабинет),
    а не банк плательщика.
    """

    payment_id: str
    status: PaymentStatus | None = None
    refund_id: str | None = None


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
    """Платёжка. Реализации: `PlategaPaymentProvider` (задача 15), `FakePaymentProvider`."""

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

    def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> PaymentNotice:
        """Номер платежа и подсказки из уведомления платёжки (задачи 14, 15).

        Не тот формат, неверная подпись или секрет — `InvalidNotification`. Сеть не трогает.
        """
        ...

    async def list_payments(self, since: datetime, until: datetime) -> Sequence[PaymentState]:
        """Платежи, созданные с `since` (включительно) до `until` — для ежедневной сверки."""
        ...
