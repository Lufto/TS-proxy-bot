"""Снимок состояния клиента — общий вход для правил сроков."""

from dataclasses import dataclass
from datetime import datetime, timedelta

NO_GRACE_EXTENSION = timedelta(0)


@dataclass(frozen=True)
class ClientSnapshot:
    """Что известно о клиенте в момент расчёта.

    `expire_at` — срок из Remnawave (в льготный период он уже включает 3 льготных дня).
    `on_trial` — текущий срок пробный, оплат после него не было.
    `grace_started_at` — отметка «льготный начался» для текущего оплаченного срока;
    при оплате сервис её снимает.
    `access_disabled` — льготный (или пробный) кончился и доступ выключен.
    `grace_extension` — на сколько продлён текущий льготный период компенсациями за сбой
    (задача 20); конец льготного — `domain.grace.grace_ends_at` с этим продлением.
    """

    expire_at: datetime | None
    on_trial: bool
    grace_started_at: datetime | None
    access_disabled: bool
    grace_extension: timedelta = NO_GRACE_EXTENSION
