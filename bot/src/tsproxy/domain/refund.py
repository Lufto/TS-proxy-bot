"""Возврат денег (задача 21; решение владельца 2026-10-30).

Возвраты — через поддержку: клиент пишет в поддержку, администратор решает и нажимает
«↩️ Вернуть» в карточке клиента. Правил «можно / нельзя» в коде нет, кроме одного: в списке
только оплаты за последние 30 дней. Возвращается **вся сумма** оплаты, а срок подписки
становится короче на весь срок, который эта оплата дала.

Исключение — случайная вторая оплата (задача 14): клиент сам может вернуть её кнопкой
в течение 7 дней, без решения администратора.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from tsproxy.domain.constants import ONE_DAY, REFUND_WINDOW_DAYS
from tsproxy.domain.dates import require_aware

REFUND_WINDOW = timedelta(days=REFUND_WINDOW_DAYS)
# Доступ после возврата выключаем «сейчас»: панель не принимает срок в прошлом,
# поэтому ставим срок чуть позже текущего момента и выключаем аккаунт (значение — агента)
REFUND_END_MARGIN = timedelta(minutes=1)


def refund_window_open(paid_at: datetime, now: datetime) -> bool:
    """Оплату можно вернуть через поддержку: с оплаты прошло не больше 30 дней."""
    require_aware(paid_at)
    require_aware(now)
    return now <= paid_at + REFUND_WINDOW


def refund_list_since(now: datetime) -> datetime:
    """С какого момента оплаты попадают в список «↩️ Вернуть»."""
    return require_aware(now) - REFUND_WINDOW


@dataclass(frozen=True)
class TermAfterRefund:
    """Срок после возврата. `end_access` — выключить доступ сразу."""

    expire_at: datetime
    end_access: bool


def term_after_refund(expire_at: datetime, refunded: timedelta, now: datetime) -> TermAfterRefund:
    """Новый срок = нынешний срок − срок, который дала возвращённая оплата.

    Не «начало оплаченного периода»: после этой оплаты могли купить ещё, те дни остаются.
    Если остаётся меньше суток (или срок уже в прошлом — клиент пользовался днями этой
    оплаты), доступ выключается сразу: иначе по окончании срока начался бы льготный
    период — бесплатные дни после возврата. Срок никогда не становится позже нынешнего.
    """
    require_aware(now)
    if refunded < timedelta(0):
        raise ValueError("срок возвращённой оплаты не может быть отрицательным")
    shortened = expire_at - refunded
    if shortened - now >= ONE_DAY:
        return TermAfterRefund(shortened, end_access=False)
    return TermAfterRefund(min(expire_at, now + REFUND_END_MARGIN), end_access=True)
