"""Возврат денег за неиспользованные полные дни (задача 12, оферта п. 2–3).

Условия: в течение 30 дней с оплаты, только при технической проблеме на нашей стороне,
не чаще раза в полгода на клиента, не при блокировке провайдером или государством.
Решение всё равно принимает администратор — функция только считает и проверяет условия.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from tsproxy.domain.constants import (
    DOUBLE_PAYMENT_REFUND_DAYS,
    KOPECK,
    ONE_DAY,
    REFUND_COOLDOWN_MONTHS,
    REFUND_WINDOW_DAYS,
)
from tsproxy.domain.dates import add_months, require_aware

REFUND_WINDOW = timedelta(days=REFUND_WINDOW_DAYS)
DOUBLE_PAYMENT_REFUND_WINDOW = timedelta(days=DOUBLE_PAYMENT_REFUND_DAYS)
MICROSECOND = timedelta(microseconds=1)
# Доступ после возврата выключаем «сейчас»: панель не принимает срок в прошлом,
# поэтому ставим срок чуть позже текущего момента и выключаем аккаунт (значение — агента)
REFUND_END_MARGIN = timedelta(minutes=1)


class RefundKind(StrEnum):
    """Какой это возврат."""

    STANDARD = "standard"  # за неиспользованные дни: 30 дней, раз в полгода
    # случайная вторая оплата: полностью, 7 дней, вне общих правил (решение владельца)
    DOUBLE_PAYMENT = "double_payment"


class RefundReason(StrEnum):
    """Почему клиент просит вернуть деньги (выбирает администратор по обращению)."""

    SERVICE_FAULT = "service_fault"  # прокси не работал по нашей вине, поддержка не помогла
    PROVIDER_BLOCK = "provider_block"  # доступ ограничил интернет-провайдер
    STATE_BLOCK = "state_block"  # доступ ограничил государственный орган
    CHANGED_MIND = "changed_mind"  # просто передумал — для этого есть пробный период


class RefundDenial(StrEnum):
    """Почему возврат не положен."""

    BLOCKED_BY_PROVIDER = "blocked_by_provider"
    BLOCKED_BY_STATE = "blocked_by_state"
    CHANGED_MIND = "changed_mind"
    WINDOW_PASSED = "window_passed"  # прошло больше 30 дней с оплаты
    TOO_FREQUENT = "too_frequent"  # прошлый возврат был меньше полугода назад
    NOTHING_UNUSED = "nothing_unused"  # не осталось ни одного полного неиспользованного дня


_REASON_DENIALS = {
    RefundReason.PROVIDER_BLOCK: RefundDenial.BLOCKED_BY_PROVIDER,
    RefundReason.STATE_BLOCK: RefundDenial.BLOCKED_BY_STATE,
    RefundReason.CHANGED_MIND: RefundDenial.CHANGED_MIND,
}


@dataclass(frozen=True)
class RefundRequest:
    """Всё, что нужно для решения по возврату за одну оплату.

    `period_start`/`period_end` — оплаченный этой оплатой срок: начало по правилу
    `subscription.new_period_start`, конец — сохранённый в заказе `grant_target_expire_at`
    (без компенсаций, начисленных позже: их не возвращаем деньгами).
    `last_refund_at` — когда клиенту одобрили прошлый возврат.
    """

    now: datetime
    reason: RefundReason
    paid_at: datetime
    paid_amount: Decimal
    period_start: datetime
    period_end: datetime
    last_refund_at: datetime | None


@dataclass(frozen=True)
class RefundDecision:
    """Итог проверки. Сумма и дни посчитаны всегда — администратору для справки."""

    denial: RefundDenial | None
    unused_days: int
    amount: Decimal

    @property
    def allowed(self) -> bool:
        return self.denial is None


def unused_full_days(period_start: datetime, period_end: datetime, now: datetime) -> int:
    """Сколько полных дней оплаченного срока ещё впереди. Неполный день не считается."""
    require_aware(now)
    if period_end <= period_start:
        raise ValueError("оплаченный срок должен быть больше нуля")
    remaining = period_end - max(now, period_start)
    if remaining <= timedelta(0):
        return 0
    return remaining // ONE_DAY


def refund_amount(
    paid_amount: Decimal, period_start: datetime, period_end: datetime, now: datetime
) -> Decimal:
    """Сумма за неиспользованные полные дни: доля оплаты, округлённая вниз до копеек."""
    days = unused_full_days(period_start, period_end, now)
    period_units = (period_end - period_start) // MICROSECOND
    share = paid_amount * days * (ONE_DAY // MICROSECOND) / period_units
    return min(share, paid_amount).quantize(KOPECK, rounding=ROUND_DOWN)


def _denial(request: RefundRequest, unused_days: int) -> RefundDenial | None:
    if request.reason in _REASON_DENIALS:
        return _REASON_DENIALS[request.reason]
    # «В течение 30 дней»: ровно 30 дней после оплаты ещё можно
    if request.now > request.paid_at + REFUND_WINDOW:
        return RefundDenial.WINDOW_PASSED
    if request.last_refund_at is not None and request.now < add_months(
        request.last_refund_at, REFUND_COOLDOWN_MONTHS
    ):
        return RefundDenial.TOO_FREQUENT
    if unused_days == 0:
        return RefundDenial.NOTHING_UNUSED
    return None


def decide_refund(request: RefundRequest) -> RefundDecision:
    """Положен ли возврат и на какую сумму."""
    require_aware(request.now)
    days = unused_full_days(request.period_start, request.period_end, request.now)
    amount = refund_amount(
        request.paid_amount, request.period_start, request.period_end, request.now
    )
    return RefundDecision(denial=_denial(request, days), unused_days=days, amount=amount)


def decide_double_payment_refund(
    now: datetime, paid_at: datetime, paid_amount: Decimal, duration: timedelta
) -> RefundDecision:
    """Случайная вторая оплата: вся сумма в течение 7 дней с оплаты, без правил
    «30 дней» и «раз в полгода». Срок укорачивается на полные дни этой оплаты
    (`duration` — срок, который она дала, `money.paid_duration`)."""
    require_aware(now)
    if duration <= timedelta(0):
        raise ValueError("оплаченный срок должен быть больше нуля")
    denial = RefundDenial.WINDOW_PASSED if now > paid_at + DOUBLE_PAYMENT_REFUND_WINDOW else None
    return RefundDecision(denial, duration // ONE_DAY, paid_amount.quantize(KOPECK, ROUND_DOWN))


def limit_to_term(decision: RefundDecision, expire_at: datetime, now: datetime) -> RefundDecision:
    """Не возвращать больше полных дней, чем осталось в сроке клиента.

    Срок мог стать короче дней заказа: перенос подписки на другой аккаунт, администратор.
    Сумма уменьшается в той же доле, округление вниз до копеек.
    """
    require_aware(now)
    days_left = max(expire_at - now, timedelta(0)) // ONE_DAY
    if days_left >= decision.unused_days:
        return decision
    amount = (decision.amount * days_left / decision.unused_days).quantize(KOPECK, ROUND_DOWN)
    denial = decision.denial
    if denial is None and days_left == 0:
        denial = RefundDenial.NOTHING_UNUSED
    return RefundDecision(denial, days_left, amount)


def next_refund_at(last_refund_at: datetime) -> datetime:
    """С какого момента клиенту снова можно вернуть деньги (раз в полгода)."""
    return add_months(last_refund_at, REFUND_COOLDOWN_MONTHS)


@dataclass(frozen=True)
class TermAfterRefund:
    """Срок после возврата. `end_access` — выключить доступ сразу (срок почти кончился)."""

    expire_at: datetime
    end_access: bool


def term_after_refund(expire_at: datetime, refunded_days: int, now: datetime) -> TermAfterRefund:
    """Новый срок = текущий срок − возвращённые дни (payment-journeys №8, Г5).

    Не «начало оплаченного периода»: после этого заказа мог быть куплен ещё один, его дни
    остаются. Если после вычета остаётся меньше суток, доступ выключается сразу: иначе
    по окончании срока начался бы льготный период — бесплатные дни после возврата.
    Срок никогда не становится позже нынешнего.
    """
    require_aware(now)
    if refunded_days < 0:
        raise ValueError("число возвращённых дней не может быть отрицательным")
    shortened = expire_at - refunded_days * ONE_DAY
    if shortened - now >= ONE_DAY:
        return TermAfterRefund(shortened, end_access=False)
    return TermAfterRefund(min(expire_at, now + REFUND_END_MARGIN), end_access=True)
