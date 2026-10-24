"""Снимок состояния клиента — общий вход для правил сроков."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ClientSnapshot:
    """Что известно о клиенте в момент расчёта.

    `expire_at` — срок из Remnawave (в льготный период он уже включает 3 льготных дня).
    `on_trial` — текущий срок пробный, оплат после него не было.
    `grace_started_at` — отметка «льготный начался» для текущего оплаченного срока;
    при оплате сервис её снимает.
    `access_disabled` — льготный (или пробный) кончился и доступ выключен.
    """

    expire_at: datetime | None
    on_trial: bool
    grace_started_at: datetime | None
    access_disabled: bool
