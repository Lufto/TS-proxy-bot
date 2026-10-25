"""Пробный период (задача 05, аудит §7.6).

7 дней, 7 ГБ, 3 устройства. Не подключался — продлеваем на 3 дня, до 5 раз,
потом прощальное сообщение. Подключался — без продлений и без льготного периода.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tsproxy.domain.constants import (
    BYTES_PER_GB,
    PAID_DEVICE_LIMIT,
    TRIAL_DAYS,
    TRIAL_DEVICE_LIMIT,
    TRIAL_EXTENSION_DAYS,
    TRIAL_MAX_EXTENSIONS,
    TRIAL_SPAM_LIMIT,
    TRIAL_SPAM_WINDOW,
    TRIAL_TRAFFIC_GB,
    UNLIMITED_TRAFFIC_BYTES,
)
from tsproxy.domain.dates import require_aware

TRIAL_PERIOD = timedelta(days=TRIAL_DAYS)
TRIAL_EXTENSION = timedelta(days=TRIAL_EXTENSION_DAYS)


@dataclass(frozen=True)
class AccessLimits:
    """Лимиты прокси-аккаунта в Remnawave. Трафик 0 — без ограничения."""

    traffic_limit_bytes: int
    device_limit: int


@dataclass(frozen=True)
class TrialGrant:
    """Что выдать клиенту при старте пробного периода."""

    expire_at: datetime
    traffic_limit_bytes: int
    device_limit: int


def start_trial(now: datetime) -> TrialGrant:
    """Пробный период с момента нажатия кнопки: 7 дней, 7 ГБ, 3 устройства."""
    require_aware(now)
    return TrialGrant(
        expire_at=now + TRIAL_PERIOD,
        traffic_limit_bytes=TRIAL_TRAFFIC_GB * BYTES_PER_GB,
        device_limit=TRIAL_DEVICE_LIMIT,
    )


def paid_access_limits() -> AccessLimits:
    """Лимиты после оплаты: трафик без ограничения, устройства как у тарифа.

    При покупке во время пробного сервис снимает лимит 7 ГБ и тег пробного (аудит §7.6).
    """
    return AccessLimits(traffic_limit_bytes=UNLIMITED_TRAFFIC_BYTES, device_limit=PAID_DEVICE_LIMIT)


def max_trial_duration() -> timedelta:
    """Самый длинный пробный период: 7 + 5 × 3 = 22 дня."""
    return TRIAL_PERIOD + TRIAL_EXTENSION * TRIAL_MAX_EXTENSIONS


class TrialExpiryAction(StrEnum):
    """Что сделать, когда кончился пробный период."""

    EXTEND = "extend"  # продлить на 3 дня и написать «мы его продлили — попробуйте»
    FAREWELL = "farewell"  # продления кончились — выключить доступ и попрощаться
    END = "end"  # подключался — выключить доступ, без продлений и без льготного
    NOT_EXPIRED = "not_expired"  # срок ещё не прошёл (повторное или запоздалое событие)


@dataclass(frozen=True)
class TrialEnd:
    """Состояние пробного периода в момент проверки.

    `ever_connected` — Remnawave знает первое подключение.
    `traffic_limit_reached` — 7 ГБ израсходованы (статус LIMITED): тоже «подключался».
    `extensions_used` — сколько продлений по 3 дня уже дали.
    """

    expire_at: datetime
    ever_connected: bool
    traffic_limit_reached: bool
    extensions_used: int


@dataclass(frozen=True)
class TrialExpiryDecision:
    """Решение по окончанию пробного. `new_expire_at` заполнен только для EXTEND."""

    action: TrialExpiryAction
    extensions_used: int
    new_expire_at: datetime | None = None


def decide_on_trial_expiry(trial: TrialEnd, now: datetime) -> TrialExpiryDecision:
    """Решить, продлить пробный, попрощаться или просто закончить.

    Продление считается от более поздней из дат «конец пробного» и «сейчас»:
    если бот проверил окончание с опозданием, клиент всё равно получает 3 полных дня.
    """
    require_aware(now)
    if trial.extensions_used < 0:
        raise ValueError("число продлений не может быть отрицательным")
    used = trial.extensions_used
    if now < trial.expire_at:
        return TrialExpiryDecision(TrialExpiryAction.NOT_EXPIRED, used)
    if trial.ever_connected or trial.traffic_limit_reached:
        return TrialExpiryDecision(TrialExpiryAction.END, used)
    if used >= TRIAL_MAX_EXTENSIONS:
        return TrialExpiryDecision(TrialExpiryAction.FAREWELL, used)
    return TrialExpiryDecision(
        TrialExpiryAction.EXTEND, used + 1, trial_extension_end(trial.expire_at, now)
    )


def trial_extension_end(expire_at: datetime, now: datetime) -> datetime:
    """До какого момента продлить пробный: 3 дня от более поздней из дат «конец» и «сейчас».

    Тот же расчёт, когда продление доделывают после сбоя панели: дни считаются от момента,
    когда доступ действительно появился.
    """
    require_aware(now)
    return max(expire_at, now) + TRIAL_EXTENSION


def recent_trials(granted_at: Iterable[datetime], now: datetime) -> list[datetime]:
    """Новые пробные за последние 10 минут — окно автовыключения (задача 28)."""
    require_aware(now)
    return [moment for moment in granted_at if moment > now - TRIAL_SPAM_WINDOW]


def trial_spam_detected(recent_count: int) -> bool:
    """Больше 200 новых пробных за 10 минут — похоже на спам, пробные пора выключить."""
    return recent_count > TRIAL_SPAM_LIMIT
