"""Обработчики проверок аккаунта, которые меняют срок, — под общей блокировкой срока.

Льготный период (задача 11) и продление пробного (задача 10) ставят срок в панели, а
льготный по окончании ещё и выключает доступ. Зачёт оплаты (задача 13) и ручная выдача
(задача 18) тоже меняют срок. Без общей блокировки оплата в момент окончания подписки
могла бы проиграть гонку: обработчик со снимком «срок истёк», сделанным до оплаты,
поставил бы срок «конец + льготные дни» поверх оплаченного или выключил бы доступ.

Поэтому: блокировка срока клиента (`term_locks` из `app.py`, ключ — id клиента в базе),
внутри — свежий аккаунт из панели, и только по нему решают обработчики.
"""

import logging
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import Client
from tsproxy.integrations.remnawave_types import AccountNotFound, RemnawaveClient
from tsproxy.services.account_checks import AccountCheck, AccountCheckHandler
from tsproxy.services.locks import KeyedLocks

logger = logging.getLogger(__name__)


class TermLockedHandlers:
    """Обёртка-обработчик: блокировка срока, перечитанный аккаунт, затем все обработчики."""

    def __init__(
        self,
        handlers: Sequence[AccountCheckHandler],
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        term_locks: KeyedLocks,
    ) -> None:
        self._handlers = tuple(handlers)
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._term_locks = term_locks

    @property
    def handlers(self) -> tuple[AccountCheckHandler, ...]:
        return self._handlers

    async def handle(self, check: AccountCheck) -> None:
        async with self._session_factory() as session:
            client_id = await session.scalar(
                select(Client.id).where(Client.telegram_id == check.telegram_id)
            )
        if client_id is None:
            logger.warning("Аккаунт клиента %s есть в панели, а в базе нет", check.telegram_id)
            return
        async with self._term_locks.hold(client_id):
            try:
                account = await self._remnawave.get_account(check.telegram_id)
            except AccountNotFound:
                logger.warning("Аккаунт клиента %s пропал из панели", check.telegram_id)
                return
            await self._run_all(replace(check, account=account))

    async def _run_all(self, check: AccountCheck) -> None:
        """Все обработчики, даже если один упал; упавшие — одной группой ошибок наверх."""
        errors: list[Exception] = []
        for handler in self._handlers:
            try:
                await handler.handle(check)
            # Ошибка одного обработчика не должна оставить без проверки остальных; не прячем:
            # `AccountChecks` запишет группу в лог, сверка поднимет тревогу и повторит
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup(f"обработчики клиента {check.telegram_id}", errors)
