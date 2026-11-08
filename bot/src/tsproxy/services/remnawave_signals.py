"""Куда направить сигнал из вебхука Remnawave (задача 09).

Вебхук сообщает только «что-то случилось» (аудит §4.2): событие о ноде — перечитать ноды,
событие о прокси-аккаунте — перечитать этот аккаунт. Остальные события (вход в панель,
биллинг нод и т.п.) боту не нужны.
"""

import logging
from dataclasses import dataclass

from tsproxy.services.account_checks import AccountChecks
from tsproxy.services.nodes import NodeMonitor

logger = logging.getLogger(__name__)

# Префиксы событий Remnawave 3.4.5 (`libs/contract/constants/events`)
NODE_EVENT_PREFIX = "node."
ACCOUNT_EVENT_PREFIXES = ("user.", "user_hwid_devices.")


@dataclass(frozen=True)
class RemnawaveSignal:
    """Что бот берёт из вебхука: имя события и имя прокси-аккаунта, если событие о нём."""

    event: str
    username: str | None


class RemnawaveSignals:
    """Передаёт сигнал проверке аккаунта или нод. Ошибки панели пробрасывает."""

    def __init__(self, accounts: AccountChecks, nodes: NodeMonitor) -> None:
        self._accounts = accounts
        self._nodes = nodes

    async def handle(self, signal: RemnawaveSignal) -> None:
        if signal.event.startswith(NODE_EVENT_PREFIX):
            await self._nodes.refresh()
            return
        if signal.event.startswith(ACCOUNT_EVENT_PREFIXES) and signal.username is not None:
            await self._accounts.on_webhook(signal.event, signal.username)
            return
        logger.info("Вебхук Remnawave %s боту не нужен — пропускаем", signal.event)
