"""Ссылка на оплату: сколько живёт и когда её можно отдать ещё раз (задача 04, правило 3)."""

from datetime import datetime, timedelta

from tsproxy.domain.constants import PAYMENT_LINK_LIFETIME
from tsproxy.domain.dates import require_aware

# Ссылку, которой осталось жить меньше, клиенту не отдаём — не успеет оплатить.
# Значение выбрано агентом, нужно подтверждение владельца.
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
    """Можно ли отдать клиенту ту же ссылку: ей осталось жить не меньше пяти минут."""
    require_aware(now)
    if expires_at is None:
        return False
    return require_aware(expires_at) - now >= PAYMENT_REUSE_MIN_REMAINING
