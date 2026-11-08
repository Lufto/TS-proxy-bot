"""Клиент сам перевыпускает ссылку подписки (задача 31): не чаще раза в сутки.

Удаление устройства только освобождает место в списке, а выгнать устройство, уже
получившее настройки, можно лишь перевыпуском ссылки (ideas.md №13). Частый перевыпуск
ломает подписку на всех устройствах клиента — поэтому предел.
"""

from datetime import datetime, timedelta

from tsproxy.domain.constants import LINK_REVOKE_COOLDOWN
from tsproxy.domain.dates import require_aware

ONE_MINUTE = timedelta(minutes=1)


def _ceil_to_minute(moment: datetime) -> datetime:
    floored = moment.replace(second=0, microsecond=0)
    return floored if floored == moment else floored + ONE_MINUTE


def link_revoke_available_at(now: datetime, last_revoked_at: datetime | None) -> datetime | None:
    """С какого момента клиенту снова можно перевыпустить ссылку. `None` — можно сейчас.

    Момент округляется вверх до минуты: клиенту время показывается без секунд, и в
    показанную минуту перевыпуск уже должен пройти.
    """
    require_aware(now)
    if last_revoked_at is None:
        return None
    available_at = _ceil_to_minute(require_aware(last_revoked_at) + LINK_REVOKE_COOLDOWN)
    return available_at if now < available_at else None
