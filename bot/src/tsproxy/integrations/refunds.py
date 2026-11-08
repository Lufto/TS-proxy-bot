"""Возврат оплаты тем же способом, которым платили (задачи 21 и 33).

`RefundRouter` выбирает способ по платёжке заказа (`orders.payment_provider`): СБП —
через платёжку (`ProviderRefunds`), звёзды — через Telegram (`TelegramStarsRefunds`).
Сервис возвратов знает только интерфейс `PaymentRefunds` и не ветвится по способу оплаты.
"""

from collections.abc import Mapping
from decimal import Decimal

from tsproxy.integrations.payments_types import (
    PaidPayment,
    PaymentProvider,
    PaymentRefunds,
    PaymentRejected,
    Refund,
)


class ProviderRefunds:
    """Возврат через платёжку (`PaymentProvider.refund`): ей нужен только номер платежа."""

    def __init__(self, provider: PaymentProvider) -> None:
        self._provider = provider

    async def refund(self, payment: PaidPayment, amount: Decimal, idempotency_key: str) -> Refund:
        return await self._provider.refund(payment.payment_id, amount, idempotency_key)


class RefundRouter:
    """Возврат по способу оплаты заказа. Способа нет (оплата выключена, платёжку сменили) —
    вернуть через бота нельзя: `supports` — `False`, `refund` — `PaymentRejected`."""

    def __init__(self, routes: Mapping[str, PaymentRefunds]) -> None:
        self._routes = dict(routes)

    def supports(self, provider: str | None) -> bool:
        return provider is not None and provider in self._routes

    async def refund(self, payment: PaidPayment, amount: Decimal, idempotency_key: str) -> Refund:
        route = self._routes.get(payment.provider)
        if route is None:
            raise PaymentRejected(f"возврат через «{payment.provider}» не подключён")
        return await route.refund(payment, amount, idempotency_key)


def refund_router(provider: PaymentProvider | None, **others: PaymentRefunds) -> RefundRouter:
    """Возвраты через платёжку (`None` — оплата выключена) и другие способы по имени
    (`telegram_stars=TelegramStarsRefunds(bot)`)."""
    routes: dict[str, PaymentRefunds] = dict(others)
    if provider is not None:
        routes[provider.name] = ProviderRefunds(provider)
    return RefundRouter(routes)
