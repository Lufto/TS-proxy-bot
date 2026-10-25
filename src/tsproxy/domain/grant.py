"""Повторы выдачи доступа, если Remnawave не ответила после оплаты.

Задача 04 п. 6 и задача 21 п. 7: заказ «оплачено, доступ не выдан», повторы каждую минуту
30 минут, затем тревога и повторы раз в 10 минут до суток. Повторы живут в базе
(`grant_attempts`, `next_grant_attempt_at`), поэтому перезапуск бота их не теряет (аудит §7.2).
Отсчёт — от момента зачёта оплаты.
"""

from dataclasses import dataclass
from datetime import datetime

from tsproxy.domain.constants import (
    GRANT_FAST_RETRIES,
    GRANT_FAST_RETRY_INTERVAL,
    GRANT_RETRY_DEADLINE,
    GRANT_SLOW_RETRY_INTERVAL,
)
from tsproxy.domain.dates import require_aware


@dataclass(frozen=True)
class GrantRetry:
    """Что делать после неудачной попытки выдачи.

    `next_attempt_at` — когда пробовать снова; `None` — сутки прошли, повторы кончились.
    `escalate` — полчаса повторов не помогли: тревога администраторам (ровно один раз).
    """

    next_attempt_at: datetime | None
    escalate: bool


def plan_grant_retry(paid_at: datetime, failed_attempts: int, now: datetime) -> GrantRetry:
    """Расписание после `failed_attempts`-й неудачной попытки (считая первую, сразу при оплате).

    Первая попытка и 30 повторов — раз в минуту; дальше — раз в 10 минут. Последняя
    попытка — ровно через сутки после зачёта, после неё повторов нет.
    """
    require_aware(paid_at)
    require_aware(now)
    if failed_attempts < 1:
        raise ValueError("число неудачных попыток считается с единицы")
    deadline = paid_at + GRANT_RETRY_DEADLINE
    if now >= deadline:
        return GrantRetry(next_attempt_at=None, escalate=False)
    fast = failed_attempts <= GRANT_FAST_RETRIES
    interval = GRANT_FAST_RETRY_INTERVAL if fast else GRANT_SLOW_RETRY_INTERVAL
    return GrantRetry(
        next_attempt_at=min(now + interval, deadline),
        escalate=failed_attempts == GRANT_FAST_RETRIES + 1,
    )
