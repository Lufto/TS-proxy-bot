"""Льготный период: 3 дня + «последний день» после конца платной подписки, по событию.

Задача 21 п. 11; «последний день» с большим предупреждением добавил владелец 2026-10-24.

Бот по событию `user.expired` (или по сверке, если событие потерялось) решает,
что делать, функцией `decide_on_expiry`. Отметку «льготный начался» сервис ставит
атомарно, поэтому повторное событие видит её и не даёт ещё 3 дня.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import GRACE_DAYS, LAST_CHANCE_DAYS
from tsproxy.domain.dates import require_aware

LAST_CHANCE_STARTS_AFTER = timedelta(days=GRACE_DAYS)
GRACE_PERIOD = timedelta(days=GRACE_DAYS + LAST_CHANCE_DAYS)


class GraceAction(StrEnum):
    """Что сделать, когда пришло событие «срок кончился» или сверка нашла окончание."""

    START_GRACE = "start_grace"  # поставить отметку и срок в Remnawave до конца льготного
    ALREADY_IN_GRACE = "already_in_grace"  # повтор события — ничего не делать
    END_GRACE = "end_grace"  # выключить доступ (аккаунт не удалять), одно финальное сообщение
    ALREADY_ENDED = "already_ended"  # доступ уже выключен — ничего не делать
    NOT_EXPIRED = "not_expired"  # срок в будущем (оплатил, продлил администратор) — ничего
    NOT_PAID_PERIOD = "not_paid_period"  # кончился пробный — решают правила пробного


@dataclass(frozen=True)
class GraceDecision:
    """Решение по льготному периоду. Даты заполнены для START_GRACE и ALREADY_IN_GRACE."""

    action: GraceAction
    grace_started_at: datetime | None = None
    grace_ends_at: datetime | None = None


def grace_ends_at(grace_started_at: datetime) -> datetime:
    """Когда кончается льготный период, начатый в `grace_started_at`."""
    return grace_started_at + GRACE_PERIOD


def _in_grace(started_at: datetime) -> GraceDecision:
    return GraceDecision(GraceAction.ALREADY_IN_GRACE, started_at, grace_ends_at(started_at))


def decide_on_expiry(client: ClientSnapshot, now: datetime) -> GraceDecision:
    """Решить, начать льготный период, ничего не делать или выключить доступ.

    Льготный период начинается с более поздней из дат «конец срока» и «сейчас»:
    если бот лежал и сверка нашла окончание позже, клиент всё равно получает
    полные 3 дня доступа.
    """
    require_aware(now)
    if client.access_disabled:
        return GraceDecision(GraceAction.ALREADY_ENDED)
    if client.expire_at is None:
        return GraceDecision(GraceAction.NOT_EXPIRED)
    expired = now >= client.expire_at
    if client.grace_started_at is not None:
        if now < grace_ends_at(client.grace_started_at):
            return _in_grace(client.grace_started_at)
        # Льготный прошёл, но срок в панели позже — его продлили, не выключаем
        return GraceDecision(GraceAction.END_GRACE if expired else GraceAction.NOT_EXPIRED)
    if not expired:
        return GraceDecision(GraceAction.NOT_EXPIRED)
    if client.on_trial:
        return GraceDecision(GraceAction.NOT_PAID_PERIOD)
    started_at = max(client.expire_at, now)
    return GraceDecision(GraceAction.START_GRACE, started_at, grace_ends_at(started_at))


def is_last_chance(grace_started_at: datetime, now: datetime) -> bool:
    """Идёт ли «последний день» льготного периода — время для большого предупреждения."""
    require_aware(grace_started_at)
    require_aware(now)
    return grace_started_at + LAST_CHANCE_STARTS_AFTER <= now < grace_ends_at(grace_started_at)
