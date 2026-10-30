"""Оплата звёздами Telegram (задача 33): цена в звёздах из рублёвой и проверка перед оплатой."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tsproxy.domain.stars import (
    STARS_CURRENCY,
    STARS_INVOICE_LIFETIME,
    StarsOrder,
    StarsRefusal,
    paid_rubles,
    pre_checkout_refusal,
    stars_in_rubles,
    stars_price,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
RATE = Decimal("1.5")


# --- Цена в звёздах: рубли / курс, до целой звезды ---


@pytest.mark.parametrize(
    ("rubles", "stars"),
    [("150", 100), ("405", 270), ("765", 510), ("1440", 960), ("15", 10)],
)
def test_owner_prices_in_stars(rubles: str, stars: int) -> None:
    assert stars_price(Decimal(rubles), RATE) == stars


def test_price_rounds_to_nearest_star() -> None:
    assert stars_price(Decimal("100"), RATE) == 67  # 66,67
    assert stars_price(Decimal("101"), Decimal(2)) == 51  # ровно половина — вверх
    assert stars_price(Decimal("100.5"), Decimal(1)) == 101


def test_price_is_at_least_one_star() -> None:
    assert stars_price(Decimal("0.1"), RATE) == 1


@pytest.mark.parametrize("rate", [Decimal(0), Decimal(-1)])
def test_rate_must_be_positive(rate: Decimal) -> None:
    with pytest.raises(ValueError, match="курс"):
        stars_price(Decimal(150), rate)


def test_stars_in_rubles_by_rate() -> None:
    assert stars_in_rubles(100, RATE) == Decimal("150.00")
    assert stars_in_rubles(67, RATE) == Decimal("100.50")
    assert stars_in_rubles(1, Decimal("1.333")) == Decimal("1.33")


# --- Сколько рублей заказа покрывает оплата звёздами ---


def test_full_payment_covers_whole_price() -> None:
    # цена заказа, а не «звёзды × курс»: при округлении звёзд срок не должен пересчитываться
    assert paid_rubles(Decimal("155"), 103, 103) == Decimal("155")


def test_partial_payment_is_proportional_down_to_kopeck() -> None:
    assert paid_rubles(Decimal("150"), 100, 50) == Decimal("75.00")
    assert paid_rubles(Decimal("100"), 3, 1) == Decimal("33.33")


def test_paid_stars_must_be_positive() -> None:
    with pytest.raises(ValueError, match="звёзд"):
        paid_rubles(Decimal("150"), 100, 0)


# --- Проверка перед оплатой (pre_checkout_query) ---


def order(
    *,
    awaiting: bool = True,
    paid: bool = False,
    created_at: datetime = NOW - timedelta(minutes=5),
    stars: int = 100,
) -> StarsOrder:
    return StarsOrder(
        awaiting_payment=awaiting, paid=paid, created_at=created_at, expected_stars=stars
    )


def test_live_order_with_right_amount_is_ok() -> None:
    assert pre_checkout_refusal(order(), STARS_CURRENCY, 100, NOW) is None


def test_unknown_order_is_refused() -> None:
    assert pre_checkout_refusal(None, STARS_CURRENCY, 100, NOW) is StarsRefusal.NOT_FOUND


def test_paid_order_is_refused() -> None:
    paid = order(awaiting=False, paid=True)
    assert pre_checkout_refusal(paid, STARS_CURRENCY, 100, NOW) is StarsRefusal.ALREADY_PAID


def test_canceled_order_is_expired() -> None:
    canceled = order(awaiting=False)
    assert pre_checkout_refusal(canceled, STARS_CURRENCY, 100, NOW) is StarsRefusal.EXPIRED


def test_old_invoice_is_expired() -> None:
    old = order(created_at=NOW - STARS_INVOICE_LIFETIME)
    assert pre_checkout_refusal(old, STARS_CURRENCY, 100, NOW) is StarsRefusal.EXPIRED


def test_invoice_just_before_expiry_is_ok() -> None:
    fresh = order(created_at=NOW - STARS_INVOICE_LIFETIME + timedelta(seconds=1))
    assert pre_checkout_refusal(fresh, STARS_CURRENCY, 100, NOW) is None


def test_other_amount_is_refused() -> None:
    # курс или цена поменялись после счёта — пусть клиент запросит новый
    assert pre_checkout_refusal(order(), STARS_CURRENCY, 99, NOW) is StarsRefusal.AMOUNT_MISMATCH


def test_other_currency_is_refused() -> None:
    assert pre_checkout_refusal(order(), "RUB", 100, NOW) is StarsRefusal.AMOUNT_MISMATCH


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="часового пояса"):
        pre_checkout_refusal(order(), STARS_CURRENCY, 100, NOW.replace(tzinfo=None))
