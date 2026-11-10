"""Ссылка на оплату: сколько живёт и когда её можно отдать ещё раз (задача 04, правило 3).

Задача 14: случайная двойная оплата и сверка оплат с платёжкой.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tsproxy.domain.constants import PAYMENT_LINK_LIFETIME
from tsproxy.domain.dates import require_aware

# Ссылку, которой осталось жить меньше, клиенту не отдаём — создаём новую (решение
# владельца 2026-11-10: ссылка Platega живёт около 10 минут, при 15 повтора не было бы вовсе).
PAYMENT_REUSE_MIN_REMAINING = timedelta(minutes=5)


def payment_link_expires_at(created_at: datetime, provider_expires_at: datetime | None) -> datetime:
    """Когда ссылка на оплату перестанет действовать.

    Час с момента создания платежа; раньше — если так сказала сама платёжка.
    """
    ours = require_aware(created_at) + PAYMENT_LINK_LIFETIME
    if provider_expires_at is None:
        return ours
    return min(ours, require_aware(provider_expires_at))


def can_reuse_payment(expires_at: datetime | None, now: datetime) -> bool:
    """Можно ли отдать клиенту ту же ссылку: ей осталось жить не меньше 5 минут."""
    require_aware(now)
    if expires_at is None:
        return False
    return require_aware(expires_at) - now >= PAYMENT_REUSE_MIN_REMAINING


# --- Случайная двойная оплата (задача 14, решение владельца 2026-10-25, п. 1) ---


def possible_double_payment(created_at: datetime, other_paid_at: datetime | None) -> bool:
    """Похоже ли, что клиент заплатил дважды случайно.

    `created_at` — когда создан засчитываемый заказ, `other_paid_at` — когда засчитан
    последний другой заказ клиента. Другой засчитан уже после создания этого — значит,
    две ссылки жили одновременно, и вторая оплата могла быть случайной. Засчитан раньше —
    клиент видел «✅ Оплачено» и покупает снова сознательно.
    """
    require_aware(created_at)
    if other_paid_at is None:
        return False
    return require_aware(other_paid_at) > created_at


# --- Сверка оплат с платёжкой (задача 14, решение владельца 2026-10-25, п. 4) ---


class ReconcileMismatch(StrEnum):
    """Расхождение между платёжкой и нашими заказами."""

    PAID_WITHOUT_ORDER = "paid_without_order"  # деньги у платёжки, заказа у нас нет
    PAID_NOT_CREDITED = "paid_not_credited"  # оплачено, а заказ не засчитан
    CREDITED_NOT_PAID = "credited_not_paid"  # засчитан, а платёжка говорит «не оплачен»
    MISSING_AT_PROVIDER = "missing_at_provider"  # засчитан, а в списке платёжки его нет
    REFUNDED_OUTSIDE_BOT = "refunded_outside_bot"  # возврат у платёжки, а в боте его нет


@dataclass(frozen=True)
class ProviderRecord:
    """Платёж из списка платёжки."""

    payment_id: str
    paid: bool  # деньги получены (в том числе если потом был возврат)
    refunded: bool  # по платежу был возврат, хотя бы частичный


@dataclass(frozen=True)
class OrderRecord:
    """Наш заказ с платежом."""

    order_id: int
    payment_id: str
    credited: bool  # оплата засчитана (оплачен, выдан, не выдан, на ручной проверке)
    refunded: bool  # возврат сделан через бота


@dataclass(frozen=True)
class ReconcileIssue:
    kind: ReconcileMismatch
    payment_id: str
    order_id: int | None


def reconcile_payments(
    provider: Sequence[ProviderRecord], orders: Sequence[OrderRecord]
) -> list[ReconcileIssue]:
    """Сравнить список платёжки с нашими заказами.

    `orders` — заказы с платежами из списка платёжки и засчитанные заказы за то же время.
    Засчитанный заказ, которого нет в списке, — `MISSING_AT_PROVIDER`: список мог
    не захватить его по времени, сервис перепроверит такой платёж по одному.
    Возврат мимо бота не засчитываем как «оплачено, не засчитано»: деньги клиенту уже вернули.
    """
    by_payment = {order.payment_id: order for order in orders}
    issues: list[ReconcileIssue] = []
    for payment in provider:
        order = by_payment.get(payment.payment_id)
        issue = _provider_issue(payment, order)
        if issue is not None:
            issues.append(ReconcileIssue(issue, payment.payment_id, _order_id(order)))
    listed = {payment.payment_id for payment in provider}
    issues.extend(
        ReconcileIssue(ReconcileMismatch.MISSING_AT_PROVIDER, order.payment_id, order.order_id)
        for order in orders
        if order.credited and order.payment_id not in listed
    )
    return issues


def _provider_issue(payment: ProviderRecord, order: OrderRecord | None) -> ReconcileMismatch | None:
    if order is None:
        return ReconcileMismatch.PAID_WITHOUT_ORDER if payment.paid else None
    if payment.refunded and not order.refunded:
        return ReconcileMismatch.REFUNDED_OUTSIDE_BOT
    if payment.paid and not order.credited and not order.refunded:
        return ReconcileMismatch.PAID_NOT_CREDITED
    if not payment.paid and order.credited:
        return ReconcileMismatch.CREDITED_NOT_PAID
    return None


def _order_id(order: OrderRecord | None) -> int | None:
    return None if order is None else order.order_id
