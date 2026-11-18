"""«Мой профиль»: статус словами, сколько осталось, трафик (задача 10, решения по экранам).

Чистые правила показа: статус из состояния клиента, остаток срока, склонение чисел
и гигабайты. Тексты — в `content/client/`, здесь только выбор формы.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import BYTES_PER_GB, HOURS_PER_DAY
from tsproxy.domain.dates import require_aware
from tsproxy.domain.subscription import SubscriptionPhase, subscription_phase

SECONDS_PER_HOUR = 3600
# Гигабайты показываем с одним знаком после запятой
GIGABYTE_STEP = Decimal("0.1")
# Склонение по последним цифрам числа: 1 день, 2–4 дня, 5–20 дней; 11–14 — всегда «дней»
FEW_ENDINGS = range(2, 5)
TEENS = range(11, 15)


class ProfileStatus(StrEnum):
    """Что написать клиенту в строке «Статус»."""

    NO_SUBSCRIPTION = "no_subscription"  # прокси-аккаунта нет
    TRIAL = "trial"  # идёт пробный период
    TRIAL_TRAFFIC_USED_UP = "trial_traffic_used_up"  # пробные 7 ГБ кончились раньше срока
    ACTIVE = "active"  # идёт оплаченный срок
    GRACE = "grace"  # оплаченный срок кончился, идёт льготный период
    EXPIRED = "expired"  # подписка закончилась
    SUSPENDED = "suspended"  # срок ещё идёт, а доступ выключен (например, администратором)


_PHASE_STATUS: dict[SubscriptionPhase, ProfileStatus] = {
    SubscriptionPhase.NEW: ProfileStatus.NO_SUBSCRIPTION,
    SubscriptionPhase.TRIAL: ProfileStatus.TRIAL,
    SubscriptionPhase.ACTIVE: ProfileStatus.ACTIVE,
    SubscriptionPhase.GRACE: ProfileStatus.GRACE,
    SubscriptionPhase.EXPIRED: ProfileStatus.EXPIRED,
}


def profile_status(
    client: ClientSnapshot, now: datetime, *, traffic_used_up: bool
) -> ProfileStatus:
    """Статус клиента для профиля.

    Доступ выключен, а срок в панели ещё не прошёл и льготного не было — это не конец
    подписки (его выключили по другой причине): «продлите» тут было бы неправдой.
    """
    phase = subscription_phase(client, now)
    suspended = (
        client.access_disabled
        and client.grace_started_at is None
        and client.expire_at is not None
        and now < client.expire_at
    )
    if suspended:
        return ProfileStatus.SUSPENDED
    if phase is SubscriptionPhase.TRIAL and traffic_used_up:
        return ProfileStatus.TRIAL_TRAFFIC_USED_UP
    return _PHASE_STATUS[phase]


@dataclass(frozen=True)
class TimeLeft:
    """Остаток срока в полных днях и часах. Оба нуля — меньше часа (или срок прошёл)."""

    days: int
    hours: int


def time_left(now: datetime, until: datetime) -> TimeLeft:
    """Сколько осталось до `until`. Неполный час отбрасывается: обещаем не больше, чем есть."""
    require_aware(now)
    require_aware(until)
    seconds = max(int((until - now).total_seconds()), 0)
    total_hours = seconds // SECONDS_PER_HOUR
    return TimeLeft(days=total_hours // HOURS_PER_DAY, hours=total_hours % HOURS_PER_DAY)


class PluralForm(StrEnum):
    """Форма слова после числа: 1 день, 2 дня, 5 дней."""

    ONE = "one"
    FEW = "few"
    MANY = "many"


def plural_form(number: int) -> PluralForm:
    """Русское склонение после числа: 1, 21, 101 — ONE; 2–4, 22 — FEW; 0, 5–20, 111 — MANY."""
    last_two = abs(number) % 100
    last = last_two % 10
    if last_two in TEENS:
        return PluralForm.MANY
    if last == 1:
        return PluralForm.ONE
    if last in FEW_ENDINGS:
        return PluralForm.FEW
    return PluralForm.MANY


def format_gigabytes(size_bytes: int) -> str:
    """Гигабайты для клиента: «2,3», «7», «0,5». Ненулевой расход не меньше «0,1»,
    иначе кажется, что трафик не тратится."""
    if size_bytes < 0:
        raise ValueError("размер не может быть отрицательным")
    value = (Decimal(size_bytes) / BYTES_PER_GB).quantize(GIGABYTE_STEP, rounding=ROUND_HALF_UP)
    if size_bytes > 0:
        value = max(value, GIGABYTE_STEP)
    text = f"{value:f}".removesuffix(".0")
    return text.replace(".", ",")
