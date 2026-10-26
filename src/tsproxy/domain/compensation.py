"""Компенсация при сбое ноды: простой + 1 день, один раз на инцидент (спецификация, аудит §7.7).

Как устроен перенос компенсации из льготного периода (задача 21 п. 13):

- Клиент с оплаченной активной подпиской получает EXTEND: абсолютную дату
  «срок до X» = текущий срок + компенсация. Сервис записывает, кому начислено
  по этому инциденту, поэтому падение на середине рассылки не даст двойной компенсации.
- Клиент в льготном периоде получает CARRY_OVER: срок в Remnawave и конец
  льготного не меняются, а компенсация копится как «перенесённая» (несколько
  инцидентов складываются). Хранится она в журнале событий: событие компенсации
  с видом CARRY_OVER после отметки «льготный начался» (задача 20, без новой колонки).
  При следующей оплате сервис передаёт эту сумму в
  `subscription.grant_target_expire_at(carried_compensation=...)`, она добавляется
  к новому сроку. Обнулять нечего: оплата снимает отметку льготного, а следующий
  льготный начнётся позже этих событий. Так покупка «от момента оплаты» не затирает
  компенсацию (аудит §7.5).
- Пробный, новый, истёкший клиент — NOT_ELIGIBLE: компенсация только оплаченного срока.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import COMPENSATION_BONUS_DAYS, ONE_HOUR, OUTAGE_MERGE_GAP
from tsproxy.domain.dates import require_aware
from tsproxy.domain.subscription import SubscriptionPhase, subscription_phase

COMPENSATION_BONUS = timedelta(days=COMPENSATION_BONUS_DAYS)
NO_TIME = timedelta(0)
# Панель хранит срок с миллисекундами: сроки ближе этого считаем одним и тем же
EXPIRY_MATCH_TOLERANCE = timedelta(seconds=1)


@dataclass(frozen=True)
class Outage:
    """Простой ноды: с какого по какой момент (UTC)."""

    started_at: datetime
    ended_at: datetime

    def __post_init__(self) -> None:
        if self.ended_at <= self.started_at:
            raise ValueError("простой должен кончаться позже, чем начался")


def merge_outages(outages: Iterable[Outage], max_gap: timedelta = OUTAGE_MERGE_GAP) -> list[Outage]:
    """Склеить «мигание» ноды: простои с перерывом не больше `max_gap` — один инцидент."""
    merged: list[Outage] = []
    for outage in sorted(outages, key=lambda item: item.started_at):
        if merged and outage.started_at - merged[-1].ended_at <= max_gap:
            last = merged[-1]
            merged[-1] = Outage(last.started_at, max(last.ended_at, outage.ended_at))
        else:
            merged.append(outage)
    return merged


def incident_of(
    outages: Iterable[Outage], target: Outage, max_gap: timedelta = OUTAGE_MERGE_GAP
) -> Outage:
    """Инцидент — склеенный простой, в который входит `target` (вместе с соседями по «миганию»).

    Простои разных нод тоже склеиваются: одновременное падение двух нод — один сбой,
    за одно и то же время компенсация не начисляется дважды.
    """
    for incident in merge_outages([*outages, target], max_gap):
        if incident.started_at <= target.started_at and target.ended_at <= incident.ended_at:
            return incident
    raise ValueError("простой не попал ни в один инцидент")  # склейка всегда его содержит


def remaining_compensation(incident: Outage, compensated: Iterable[Outage]) -> timedelta:
    """Сколько ещё начислить по инциденту, если часть его простоев уже компенсирована.

    Весь инцидент стоит `compensation_duration(incident)`; уже начисленное — сумма по
    компенсированным частям (каждая со своим днём сверху). Ноль — новое уже покрыто.
    """
    paid = sum((compensation_duration(part) for part in merge_outages(compensated)), NO_TIME)
    return max(compensation_duration(incident) - paid, NO_TIME)


def compensation_duration(outage: Outage) -> timedelta:
    """Длительность простоя, округлённая вверх до часа, плюс 1 день."""
    hours = -(-(outage.ended_at - outage.started_at) // ONE_HOUR)  # деление с округлением вверх
    return ONE_HOUR * hours + COMPENSATION_BONUS


class CompensationAction(StrEnum):
    """Что сделать с клиентом при нажатии «Компенсировать»."""

    EXTEND = "extend"  # поставить срок до `extend_to`
    CARRY_OVER = "carry_over"  # в льготном: запомнить `carry_over` до следующей оплаты
    ALREADY_COMPENSATED = "already_compensated"  # по этому инциденту уже начислено
    NOT_ELIGIBLE = "not_eligible"  # нет оплаченной подписки


@dataclass(frozen=True)
class CompensationPlan:
    """Решение по одному клиенту для одного инцидента."""

    action: CompensationAction
    extend_to: datetime | None = None
    carry_over: timedelta | None = None


def plan_compensation(
    client: ClientSnapshot, amount: timedelta, *, already_compensated: bool, now: datetime
) -> CompensationPlan:
    """Решить, как начислить компенсацию `amount` клиенту (состояние — на момент начисления)."""
    if amount <= timedelta(0):
        raise ValueError("компенсация должна быть больше нуля")
    if already_compensated:
        return CompensationPlan(CompensationAction.ALREADY_COMPENSATED)
    phase = subscription_phase(client, now)
    if phase is SubscriptionPhase.ACTIVE and client.expire_at is not None:
        return CompensationPlan(CompensationAction.EXTEND, extend_to=client.expire_at + amount)
    if phase is SubscriptionPhase.GRACE:
        return CompensationPlan(CompensationAction.CARRY_OVER, carry_over=amount)
    return CompensationPlan(CompensationAction.NOT_ELIGIBLE)


class ResumeAction(StrEnum):
    """Начисление клиенту записано, а срок в панели — неизвестно, поставлен ли (сбой посреди)."""

    APPLY = "apply"  # срок прежний — поставить
    ALREADY_APPLIED = "already_applied"  # срок уже новый (панель приняла, ответ потерялся)
    UNCLEAR = "unclear"  # срок изменился иначе — решает администратор


def resume_extension(current: datetime, before: datetime, target: datetime) -> ResumeAction:
    """Как продолжить прерванное продление «с `before` до `target`», если сейчас срок `current`."""
    for moment in (current, before, target):
        require_aware(moment)
    if abs(current - target) < EXPIRY_MATCH_TOLERANCE:
        return ResumeAction.ALREADY_APPLIED
    if abs(current - before) < EXPIRY_MATCH_TOLERANCE:
        return ResumeAction.APPLY
    return ResumeAction.UNCLEAR
