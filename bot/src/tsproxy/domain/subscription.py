"""Состояние подписки и от какой даты считать новый оплаченный срок (задача 04, правило 2)."""

from datetime import datetime, timedelta
from enum import StrEnum

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.dates import require_aware
from tsproxy.domain.grace import grace_ends_at


class SubscriptionPhase(StrEnum):
    """В каком состоянии клиент сейчас."""

    NEW = "new"  # прокси-аккаунта ещё нет
    TRIAL = "trial"  # идёт пробный период
    ACTIVE = "active"  # идёт оплаченный срок
    GRACE = "grace"  # оплаченный срок кончился, идут льготные дни (3 + «последний»)
    EXPIRED = "expired"  # доступа нет или срок уже прошёл


def subscription_phase(client: ClientSnapshot, now: datetime) -> SubscriptionPhase:
    """Состояние клиента в момент `now`. Ровно в момент окончания срок считается истёкшим."""
    require_aware(now)
    if client.access_disabled:
        return SubscriptionPhase.EXPIRED
    if client.expire_at is None:
        return SubscriptionPhase.NEW
    if client.grace_started_at is not None:
        in_grace = now < grace_ends_at(client.grace_started_at, client.grace_extension)
        return SubscriptionPhase.GRACE if in_grace else SubscriptionPhase.EXPIRED
    if now >= client.expire_at:
        return SubscriptionPhase.EXPIRED
    return SubscriptionPhase.TRIAL if client.on_trial else SubscriptionPhase.ACTIVE


def new_period_start(
    phase: SubscriptionPhase, now: datetime, expire_at: datetime | None
) -> datetime:
    """С какого момента начинается новый оплаченный срок.

    Активная подписка или пробный — с текущего окончания (дни добавляются).
    Льготный период, истёкшая подписка, новый клиент — с момента оплаты:
    проведённые в льготном дни — подарок. Срок в Remnawave в льготный период
    уже сдвинут на 3 дня, поэтому его здесь не используем (аудит §7.5).
    """
    require_aware(now)
    if phase in (SubscriptionPhase.ACTIVE, SubscriptionPhase.TRIAL) and expire_at is not None:
        # Срок мог пройти, а событие об окончании ещё не дошло — тогда от момента оплаты
        return max(expire_at, now)
    return now


def grant_target_expire_at(
    client: ClientSnapshot, paid_at: datetime, duration: timedelta
) -> datetime:
    """«Продлить до X» — абсолютная дата, которую считаем в момент оплаты и храним в заказе.

    В Remnawave передаём «поставь срок до X», поэтому повтор выдачи безопасен (задача 21 п. 6).
    Компенсация за сбой в льготный период продлевает сам льготный и к оплате не добавляется
    (решение владельца 2026-10-26).
    """
    if duration <= timedelta(0):
        raise ValueError("оплаченный срок должен быть больше нуля")
    phase = subscription_phase(client, paid_at)
    start = new_period_start(phase, paid_at, client.expire_at)
    return start + duration
