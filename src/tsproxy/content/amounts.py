"""Сумма оплаты текстом: «150 ₽» или «100 ⭐» — тем, чем клиент платил (задача 33).

Слова и значки — в `units.rubles` и `units.stars` (оба файла текстов).
"""

from decimal import Decimal

from tsproxy.content.loader import Texts
from tsproxy.domain.money import format_rubles


def paid_text(texts: Texts, amount: Decimal, stars: int | None) -> str:
    """Оплачено звёздами — звёзды, иначе рубли (`amount`)."""
    if stars is not None:
        return texts.get("units.stars", stars=stars)
    return texts.get("units.rubles", amount=format_rubles(amount))
