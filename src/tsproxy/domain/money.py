"""Деньги: срок за фактически оплаченную сумму и скидка от цены месяца. Только `Decimal`."""

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from tsproxy.domain.constants import HOURS_PER_DAY, MONTH_DAYS, PRORATION_BONUS_HOURS

PERCENT = Decimal(100)


def _check_tariff(days: int, price: Decimal) -> None:
    if days <= 0:
        raise ValueError("длительность тарифа должна быть больше нуля")
    if price <= 0:
        raise ValueError("цена тарифа должна быть больше нуля")


def paid_duration(tariff_days: int, tariff_price: Decimal, paid_amount: Decimal) -> timedelta:
    """Срок, который получает клиент за фактически оплаченную сумму (задача 04, правило 8).

    Оплачено ровно по цене — ровно дни тарифа. Иначе (недоплата или переплата из-за
    сбоя платёжки) — пропорционально по цене за день заказанного тарифа, с округлением
    вверх до целого часа и плюс 2 часа сверху.
    """
    _check_tariff(tariff_days, tariff_price)
    if paid_amount <= 0:
        raise ValueError("оплаченная сумма должна быть больше нуля")
    if paid_amount == tariff_price:
        return timedelta(days=tariff_days)
    exact_hours = paid_amount * tariff_days * HOURS_PER_DAY / tariff_price
    hours = int(exact_hours.to_integral_value(rounding=ROUND_CEILING))
    return timedelta(hours=hours + PRORATION_BONUS_HOURS)


def discount_percent(tariff_days: int, tariff_price: Decimal, monthly_price: Decimal) -> int:
    """Скидка тарифа в процентах от цены месяца, для показа клиенту.

    Считаем по целым месяцам, как в оффере: 365 дней — это 12 месяцев, 1440 ₽ против
    12 × 150 = 1800 ₽ → 20. Округляем вниз, чтобы не обещать больше, чем есть.
    Тариф короче месяца или дороже месячного в пересчёте — скидка 0, не отрицательная.
    """
    _check_tariff(tariff_days, tariff_price)
    _check_tariff(MONTH_DAYS, monthly_price)
    months = tariff_days // MONTH_DAYS
    if months == 0:
        return 0
    full_price = monthly_price * months
    percent = (1 - tariff_price / full_price) * PERCENT
    return max(0, int(percent.to_integral_value(rounding=ROUND_FLOOR)))
