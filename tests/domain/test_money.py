from datetime import timedelta
from decimal import Decimal

import pytest

from tsproxy.domain.money import discount_percent, format_rubles, paid_duration

# --- Оплата не на сумму тарифа (задача 04, правило 8) ---


def test_exact_payment_gives_full_tariff_without_bonus() -> None:
    assert paid_duration(30, Decimal("150"), Decimal("150")) == timedelta(days=30)


def test_exact_payment_with_kopecks_notation_is_still_exact() -> None:
    assert paid_duration(90, Decimal("405"), Decimal("405.00")) == timedelta(days=90)


def test_underpayment_exact_hours_plus_two() -> None:
    # 100 ₽ при 5 ₽/день = 20 дней = 480 часов, +2 часа
    assert paid_duration(30, Decimal("150"), Decimal("100")) == timedelta(hours=482)


def test_overpayment_rounds_up_to_hour_plus_two() -> None:
    # 151 ₽ = 30,2 дня = 724,8 часа → 725 → +2
    assert paid_duration(30, Decimal("150"), Decimal("151")) == timedelta(hours=727)


def test_fractional_price_per_day_rounds_up() -> None:
    # 400 ₽ по тарифу 405 ₽ / 90 дней = 2133,33 часа → 2134 → +2
    assert paid_duration(90, Decimal("405"), Decimal("400")) == timedelta(hours=2136)


def test_one_kopeck_gives_at_least_one_hour_plus_two() -> None:
    assert paid_duration(30, Decimal("150"), Decimal("0.01")) == timedelta(hours=3)


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1")])
def test_non_positive_payment_rejected(amount: Decimal) -> None:
    with pytest.raises(ValueError):
        paid_duration(30, Decimal("150"), amount)


def test_bad_tariff_rejected() -> None:
    with pytest.raises(ValueError):
        paid_duration(0, Decimal("150"), Decimal("150"))
    with pytest.raises(ValueError):
        paid_duration(30, Decimal("0"), Decimal("150"))


# --- Скидка от цены месяца, показываем клиенту ---


def test_discount_three_months() -> None:
    # 405 за 90 дней против 150 × 3 = 450 → 10%
    assert discount_percent(90, Decimal("405"), Decimal("150")) == 10


def test_discount_six_months() -> None:
    assert discount_percent(180, Decimal("765"), Decimal("150")) == 15


def test_discount_year_rounds_down() -> None:
    # скидка считается по целым месяцам, как в оффере: 1440 против 12 × 150 = 1800 → 20%
    assert discount_percent(365, Decimal("1440"), Decimal("150")) == 20


def test_discount_month_itself_is_zero() -> None:
    assert discount_percent(30, Decimal("150"), Decimal("150")) == 0


def test_more_expensive_than_month_is_zero_not_negative() -> None:
    # Тариф 15 ₽ / 1 день дороже месячного в пересчёте
    assert discount_percent(1, Decimal("15"), Decimal("150")) == 0


def test_discount_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        discount_percent(0, Decimal("15"), Decimal("150"))
    with pytest.raises(ValueError):
        discount_percent(30, Decimal("15"), Decimal("0"))


# --- Цена для клиента ---


@pytest.mark.parametrize(
    ("amount", "shown"),
    [
        (Decimal("150"), "150"),
        (Decimal("15"), "15"),
        (Decimal("405.00"), "405"),
        (Decimal("149.90"), "149,90"),
        (Decimal("1440"), "1 440"),
        (Decimal("1234567.5"), "1 234 567,50"),
    ],
)
def test_format_rubles(amount: Decimal, shown: str) -> None:
    assert format_rubles(amount) == shown


def test_format_rubles_rejects_fractions_of_kopeck() -> None:
    with pytest.raises(ValueError, match="копейк"):
        format_rubles(Decimal("1.005"))


@pytest.mark.parametrize(
    ("amount", "shown"),
    [
        (Decimal("-299.50"), "−299,50"),
        (Decimal("-0.50"), "−0,50"),
        (Decimal("-1440"), "−1\u00a0440"),
    ],
)
def test_format_rubles_negative_for_stats(amount: Decimal, shown: str) -> None:
    # задача 21: в статистике оплаты за вычетом возвратов бывают меньше нуля
    assert format_rubles(amount) == shown
