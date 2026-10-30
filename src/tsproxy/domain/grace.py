"""Льготный период: 3 дня + «последний день» после конца платной подписки, по событию.

Задача 21 п. 11; «последний день» с большим предупреждением добавил владелец 2026-10-24.

Бот по событию `user.expired` (или по сверке, если событие потерялось) решает,
что делать, функцией `decide_on_expiry`. Отметку «льготный начался» сервис ставит
атомарно, поэтому повторное событие видит её и не даёт ещё 3 дня.

Пока идёт льготный период, раз в сутки клиенту положено одно сообщение
(`grace_notice`): в первый день — «подписка кончилась», затем напоминания,
в «последний день» — большое предупреждение, после — одно финальное.

Компенсация за сбой во время льготного (задача 20, решение владельца 2026-10-26) продлевает
сам льготный период: конец сдвигается на `extension`, «последний день» — последние сутки
перед новым концом.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tsproxy.domain.client_state import NO_GRACE_EXTENSION, ClientSnapshot
from tsproxy.domain.constants import GRACE_DAYS, LAST_CHANCE_DAYS, ONE_DAY
from tsproxy.domain.dates import require_aware

LAST_CHANCE = timedelta(days=LAST_CHANCE_DAYS)
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


def grace_ends_at(
    grace_started_at: datetime, extension: timedelta = NO_GRACE_EXTENSION
) -> datetime:
    """Когда кончается льготный период, начатый в `grace_started_at` и продлённый на `extension`."""
    if extension < timedelta(0):
        raise ValueError("продление льготного не может быть отрицательным")
    return grace_started_at + GRACE_PERIOD + extension


def observed_grace_extension(grace_started_at: datetime | None, expire_at: datetime) -> timedelta:
    """Продление льготного по сроку в панели — для экранов, которые не читают журнал.

    В льготный период бот держит срок в панели равным концу льготного (с продлением),
    поэтому всё, что позже обычного конца, — продление. Обработчик льготного так не
    считает: он берёт продление из журнала (`services.compensation_log.grace_extension`).
    """
    if grace_started_at is None:
        return NO_GRACE_EXTENSION
    return max(expire_at - grace_ends_at(grace_started_at), NO_GRACE_EXTENSION)


def _in_grace(started_at: datetime, extension: timedelta) -> GraceDecision:
    return GraceDecision(
        GraceAction.ALREADY_IN_GRACE, started_at, grace_ends_at(started_at, extension)
    )


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
        if now < grace_ends_at(client.grace_started_at, client.grace_extension):
            return _in_grace(client.grace_started_at, client.grace_extension)
        # Льготный прошёл, но срок в панели позже — его продлили, не выключаем
        return GraceDecision(GraceAction.END_GRACE if expired else GraceAction.NOT_EXPIRED)
    if not expired:
        return GraceDecision(GraceAction.NOT_EXPIRED)
    if client.on_trial:
        return GraceDecision(GraceAction.NOT_PAID_PERIOD)
    started_at = max(client.expire_at, now)
    return GraceDecision(GraceAction.START_GRACE, started_at, grace_ends_at(started_at))


def is_last_chance(
    grace_started_at: datetime, now: datetime, extension: timedelta = NO_GRACE_EXTENSION
) -> bool:
    """Идёт ли «последний день» льготного периода — время для большого предупреждения."""
    require_aware(grace_started_at)
    require_aware(now)
    ends_at = grace_ends_at(grace_started_at, extension)
    return ends_at - LAST_CHANCE <= now < ends_at


class GraceStage(StrEnum):
    """Какое сообщение о льготном периоде сейчас положено клиенту."""

    STARTED = "started"  # первый день: подписка кончилась, доступ сохранён
    REMINDER = "reminder"  # следующие льготные дни: ежедневное напоминание
    LAST_DAY = "last_day"  # «последний день»: большое предупреждение
    ENDED = "ended"  # доступ выключен — одно финальное сообщение


@dataclass(frozen=True)
class GraceNotice:
    """Сообщение о льготном периоде. `due_at` — с какого момента его пора слать;
    у каждого дня свой `due_at`, по нему сервис не даёт прислать одно и то же дважды."""

    stage: GraceStage
    due_at: datetime


def grace_notice(
    grace_started_at: datetime, now: datetime, extension: timedelta = NO_GRACE_EXTENSION
) -> GraceNotice:
    """Сообщение, положенное клиенту сейчас: одно на каждые льготные сутки.

    «Последний день» — последние сутки перед концом (с продлением компенсацией).
    Если бот лежал и пропустил дни — только сегодняшнее, без пачки пропущенных.
    """
    require_aware(grace_started_at)
    require_aware(now)
    ends_at = grace_ends_at(grace_started_at, extension)
    if now >= ends_at:
        return GraceNotice(GraceStage.ENDED, ends_at)
    last_chance_at = ends_at - LAST_CHANCE
    if now >= last_chance_at:
        return GraceNotice(GraceStage.LAST_DAY, last_chance_at)
    day = max((now - grace_started_at) // ONE_DAY, 0)
    due_at = grace_started_at + day * ONE_DAY
    if day == 0:
        return GraceNotice(GraceStage.STARTED, due_at)
    return GraceNotice(GraceStage.REMINDER, due_at)
