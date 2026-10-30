"""Перенос подписки на другой Telegram-аккаунт (спецификация, админка п. 8; задача 21).

Переносится только оставшийся оплаченный срок: пробный и льготные дни — не оплата.
Новый аккаунт получает его как покупку (`subscription.new_period_start`): идущая подписка
или пробный — дни добавляются к окончанию, иначе — от «сейчас». Старый аккаунт
выключается сразу: срок «чуть позже сейчас» (панель не принимает срок в прошлом).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.dates import require_aware
from tsproxy.domain.refund import REFUND_END_MARGIN
from tsproxy.domain.subscription import SubscriptionPhase, new_period_start, subscription_phase


@dataclass(frozen=True)
class TransferPlan:
    left: timedelta  # сколько оплаченного срока переносится
    target_expire_at: datetime  # срок нового аккаунта после переноса
    source_expire_at: datetime  # срок старого аккаунта — сразу, затем доступ выключается


def plan_transfer(
    source: ClientSnapshot, target: ClientSnapshot, now: datetime
) -> TransferPlan | None:
    """Что и куда переносить. `None` — у старого аккаунта нет идущей оплаченной подписки."""
    require_aware(now)
    if subscription_phase(source, now) is not SubscriptionPhase.ACTIVE:
        return None
    if source.expire_at is None:
        return None
    left = source.expire_at - now
    start = new_period_start(subscription_phase(target, now), now, target.expire_at)
    return TransferPlan(left, start + left, now + REFUND_END_MARGIN)
