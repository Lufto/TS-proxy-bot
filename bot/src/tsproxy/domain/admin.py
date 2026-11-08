"""Админка (задача 18): кого искать, предел ручной выдачи и до какой даты продлить.

Ошибки администраторов (аудит R22): поиск — по Telegram ID или @username, действие —
всегда по Telegram ID из карточки (@username мог смениться); ручная выдача — не больше
`MAX_MANUAL_GRANT_DAYS` за раз.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import MAX_MANUAL_GRANT_DAYS
from tsproxy.domain.dates import require_aware
from tsproxy.domain.subscription import grant_target_expire_at

# Telegram ID — положительное число, в базе — BIGINT
MAX_TELEGRAM_ID = 2**63 - 1
TELEGRAM_ID_RE = re.compile(r"\d{1,19}")
# @username в Telegram: латиница, цифры и «_», начинается с буквы, до 32 символов
USERNAME_RE = re.compile(r"@?([A-Za-z]\w{0,31})", re.ASCII)


class GrantDaysOutOfRange(ValueError):
    """Число дней ручной выдачи вне предела 1…`MAX_MANUAL_GRANT_DAYS`."""


@dataclass(frozen=True)
class ClientQuery:
    """Кого искать: ровно одно из полей. `username` — без «@», в нижнем регистре."""

    telegram_id: int | None = None
    username: str | None = None


def parse_client_query(text: str) -> ClientQuery | None:
    """`123456789`, `@ivan` или `ivan`. `None` — ни ID, ни @username."""
    text = text.strip()
    if TELEGRAM_ID_RE.fullmatch(text):
        telegram_id = int(text)
        if 0 < telegram_id <= MAX_TELEGRAM_ID:
            return ClientQuery(telegram_id=telegram_id)
        return None
    match = USERNAME_RE.fullmatch(text)
    if match is None:
        return None
    # @username в Telegram не различает регистр
    return ClientQuery(username=match.group(1).lower())


def check_grant_days(days: int) -> None:
    """Бросает `GrantDaysOutOfRange`, если дней меньше 1 или больше предела."""
    if not 1 <= days <= MAX_MANUAL_GRANT_DAYS:
        raise GrantDaysOutOfRange(f"Дней должно быть от 1 до {MAX_MANUAL_GRANT_DAYS}: {days}")


def manual_grant_target(client: ClientSnapshot, now: datetime, days: int) -> datetime:
    """До какой даты продлить при ручной выдаче на `days` дней.

    Как при оплате (`grant_target_expire_at`): активная подписка или пробный — дни
    добавляются к окончанию; льготный, истёкшая, новый клиент — от текущего момента.
    Срок никогда не сокращается: 1 день в льготный период, когда льготных осталось 2,
    не отнимает у клиента уже выданный доступ.
    """
    require_aware(now)
    check_grant_days(days)
    target = grant_target_expire_at(client, now, timedelta(days=days))
    if client.expire_at is not None:
        return max(target, client.expire_at)
    return target
