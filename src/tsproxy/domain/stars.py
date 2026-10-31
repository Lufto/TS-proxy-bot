"""Оплата звёздами Telegram (задача 33; решения владельца 2026-10-30).

Цена в звёздах не пишется в файл тарифов отдельно: она считается из рублёвой по одному
курсу `stars_rub_rate` из `tariffs.yaml` (1,5 ₽ за звезду: 150 ₽ → 100 ⭐) и округляется
до целой звезды. Поменять курс — поменять одно число.

Рубли остаются главной мерой заказа: срок, пропорции и возвраты считаются от цены заказа
в рублях. Оплата звёздами переводится в рубли заказа (`paid_rubles`): полная оплата —
ровно цена заказа, так что округление звёзд никогда не пересчитывает срок.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import StrEnum

from tsproxy.domain.constants import KOPECK, PAYMENT_LINK_LIFETIME
from tsproxy.domain.dates import require_aware

# Валюта звёзд Telegram в счетах и платежах
STARS_CURRENCY = "XTR"
# Счёт в звёздах действует столько же, сколько ссылка на оплату СБП (решение агента)
STARS_INVOICE_LIFETIME = PAYMENT_LINK_LIFETIME
MIN_STARS = 1


def _check_rate(rate: Decimal) -> None:
    if rate <= 0:
        raise ValueError(f"курс звезды должен быть больше нуля, а не {rate}")


def stars_price(rubles: Decimal, rate: Decimal) -> int:
    """Цена в звёздах: рубли / курс, до ближайшей целой звезды (половина — вверх), не меньше 1."""
    _check_rate(rate)
    stars = int((rubles / rate).to_integral_value(rounding=ROUND_HALF_UP))
    return max(MIN_STARS, stars)


def stars_in_rubles(stars: int, rate: Decimal) -> Decimal:
    """Звёзды «примерно в рублях» по курсу — для статистики, до копейки."""
    _check_rate(rate)
    return (stars * rate).quantize(KOPECK, rounding=ROUND_HALF_UP)


def paid_rubles(price: Decimal, expected_stars: int, paid_stars: int) -> Decimal:
    """Сколько рублей заказа покрывает оплата звёздами.

    Полная оплата — ровно цена заказа. Иначе (такого не должно быть: сумму проверяет
    `pre_checkout_refusal`) — пропорционально, вниз до копейки: срок пересчитается как при
    оплате не на сумму тарифа (задача 04 п. 8).
    """
    if paid_stars <= 0 or expected_stars <= 0:
        raise ValueError(f"число звёзд должно быть больше нуля: {paid_stars} из {expected_stars}")
    if paid_stars == expected_stars:
        return price
    return (price * paid_stars / expected_stars).quantize(KOPECK, rounding=ROUND_FLOOR)


class StarsRefusal(StrEnum):
    """Почему счёт в звёздах нельзя оплатить сейчас (ответ на `pre_checkout_query`)."""

    NOT_FOUND = "not_found"  # нет такого заказа звёздами у этого клиента
    ALREADY_PAID = "already_paid"  # заказ уже оплачен
    EXPIRED = "expired"  # счёт устарел — запросить новый
    AMOUNT_MISMATCH = "amount_mismatch"  # сумма или валюта не та (поменялись курс или цена)


@dataclass(frozen=True)
class StarsOrder:
    """Заказ глазами правил оплаты звёздами."""

    awaiting_payment: bool  # статус «создан»
    paid: bool  # оплата уже засчитана (или возвращена)
    created_at: datetime
    expected_stars: int  # цена заказа в звёздах по нынешнему курсу


def pre_checkout_refusal(
    order: StarsOrder | None, currency: str, total_amount: int, now: datetime
) -> StarsRefusal | None:
    """Можно ли оплатить счёт: заказ жив, не оплачен, сумма в звёздах совпадает. `None` — можно."""
    require_aware(now)
    if order is None:
        return StarsRefusal.NOT_FOUND
    if order.paid:
        return StarsRefusal.ALREADY_PAID
    expires_at = require_aware(order.created_at) + STARS_INVOICE_LIFETIME
    if not order.awaiting_payment or now >= expires_at:
        return StarsRefusal.EXPIRED
    if currency != STARS_CURRENCY or total_amount != order.expected_stars:
        return StarsRefusal.AMOUNT_MISMATCH
    return None
