"""Напоминания перед окончанием доступа и сообщения о трафике пробного (задача 10).

За день и за час до окончания срока клиенту приходит напоминание с кнопкой «Продлить».
Текст различает пробный и оплаченный срок.

- Пробный — только если клиент подключался (задача 05): не подключавшемуся пробный
  продлевают сами (`domain/trial.py`). Кончился пробный трафик — напоминать о сроке незачем.
- Льготный период — не здесь: задача 11 шлёт в нём своё напоминание каждый день,
  «последний день» и финальное сообщение. Иначе клиент получил бы предупреждения дважды.

Трафик пробного (решение владельца по ideas.md №1): израсходовано 80% — предупреждение,
трафик кончился — сообщение с кнопкой «Тарифы».

Какое уведомление уже ушло, правила не знают: «ровно один раз» обеспечивает таблица
уведомлений, ключ — вид уведомления и момент окончания (`Reminder.ends_at`).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import ONE_DAY, ONE_HOUR, PERCENT, TRIAL_TRAFFIC_WARNING_PERCENT
from tsproxy.domain.dates import require_aware
from tsproxy.domain.subscription import SubscriptionPhase, subscription_phase


class AccessPeriod(StrEnum):
    """Какой срок кончается — от этого зависит текст напоминания."""

    TRIAL = "trial"
    PAID = "paid"


class ReminderKind(StrEnum):
    DAY_BEFORE = "day_before"
    HOUR_BEFORE = "hour_before"


@dataclass(frozen=True)
class Reminder:
    """Напоминание, которое пора отправить. `ends_at` — когда кончается срок."""

    kind: ReminderKind
    period: AccessPeriod
    ends_at: datetime


def _reminded_period(
    phase: SubscriptionPhase, *, ever_connected: bool, traffic_used_up: bool
) -> AccessPeriod | None:
    """О каком сроке напоминать. `None` — напоминать не о чем (в том числе в льготный)."""
    if phase is SubscriptionPhase.TRIAL:
        return AccessPeriod.TRIAL if ever_connected and not traffic_used_up else None
    if phase is SubscriptionPhase.ACTIVE:
        return AccessPeriod.PAID
    return None


def due_reminder(
    client: ClientSnapshot, now: datetime, *, ever_connected: bool, traffic_used_up: bool
) -> Reminder | None:
    """Напоминание, которое пора отправить сейчас, или `None`.

    В последний час — только «за час»: если «за день» не успело уйти (бот лежал,
    срок купили меньше чем на день), догонять его уже поздно.
    """
    require_aware(now)
    period = _reminded_period(
        subscription_phase(client, now),
        ever_connected=ever_connected,
        traffic_used_up=traffic_used_up,
    )
    if period is None or client.expire_at is None:
        return None
    ends_at = client.expire_at
    left = ends_at - now
    if left <= ONE_HOUR:
        return Reminder(ReminderKind.HOUR_BEFORE, period, ends_at)
    if left <= ONE_DAY:
        return Reminder(ReminderKind.DAY_BEFORE, period, ends_at)
    return None


class TrialTrafficNotice(StrEnum):
    WARNING = "warning"  # израсходовано 80% пробного трафика
    USED_UP = "used_up"  # пробный трафик кончился, доступа нет


def trial_traffic_notice(
    client: ClientSnapshot,
    now: datetime,
    *,
    used_bytes: int,
    limit_bytes: int,
    traffic_used_up: bool,
) -> TrialTrafficNotice | None:
    """Что сказать о трафике пробного. Только пока идёт пробный и у него есть лимит.

    `traffic_used_up` — панель уже отключила аккаунт по трафику (статус LIMITED):
    счётчик трафика может чуть отставать, а доступа уже нет.
    """
    require_aware(now)
    if subscription_phase(client, now) is not SubscriptionPhase.TRIAL or limit_bytes <= 0:
        return None
    if traffic_used_up or used_bytes >= limit_bytes:
        return TrialTrafficNotice.USED_UP
    if used_bytes * PERCENT >= limit_bytes * TRIAL_TRAFFIC_WARNING_PERCENT:
        return TrialTrafficNotice.WARNING
    return None
