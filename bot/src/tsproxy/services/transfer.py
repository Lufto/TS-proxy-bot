"""Перенос подписки на другой Telegram-аккаунт (спецификация, админка п. 8; задача 21).

Только с подтверждением платежа: администратор вводит номер платежа из чека клиента, и он
должен совпасть с оплаченным заказом старого аккаунта с выданным доступом. Так подписку
не «уведут» просьбой в поддержку от чужого человека.

Как в ручной выдаче (задача 18): предпросмотр «сейчас / станет» → «✅ Да» с увиденными
сроками обоих аккаунтов. Сроки уже другие (двойное нажатие, оплата) — ничего не делается.
Оба срока меняются под блокировками срока обоих клиентов (по порядку id — без взаимной
блокировки), аккаунты перечитываются уже под ними.

Заказы остаются у старого аккаунта. Вернуть через бота оплату, сделанную до переноса,
нельзя (`services.refunds`, отказ «подписку перенесли»): её дни уже у нового аккаунта.
Пока идёт возврат оплаты старого аккаунта, перенос не делается.
"""

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Order, RefundRequest
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.transfer import TransferPlan, plan_transfer
from tsproxy.domain.trial import paid_access_limits
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    ProxyAccount,
    RemnawaveClient,
    RemnawaveError,
)
from tsproxy.services.admin import AdminActionKind, same_second
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.grace import client_snapshot
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.refunds import OPEN_STATUSES

logger = logging.getLogger(__name__)

# Старый аккаунт не отключился — тревога на каждый перенос
TRANSFER_ALERT = "transfer_source_not_ended"


class TransferRefusal(StrEnum):
    SAME_CLIENT = "same_client"
    SOURCE_NOT_FOUND = "source_not_found"
    TARGET_NOT_FOUND = "target_not_found"
    PAYMENT_NOT_FOUND = "payment_not_found"  # платёж не подтверждает оплату старого аккаунта
    NOTHING_TO_TRANSFER = "nothing_to_transfer"  # нет идущей оплаченной подписки
    REFUND_OPEN = "refund_open"  # идёт возврат оплаты этого клиента
    CHANGED = "changed"  # сроки уже не те, что в предпросмотре


@dataclass(frozen=True)
class TransferRefused:
    refusal: TransferRefusal


@dataclass(frozen=True)
class TransferPreview:
    from_telegram_id: int
    to_telegram_id: int
    payment_id: str
    order_id: int
    from_expire_at: datetime
    to_expire_at: datetime | None  # None — у нового аккаунта нет прокси-аккаунта
    left: timedelta
    new_expire_at: datetime


@dataclass(frozen=True)
class TransferDone:
    from_telegram_id: int
    to_expire_at: datetime


@dataclass(frozen=True)
class _Prepared:
    source: Client
    target: Client
    order: Order
    source_account: ProxyAccount
    target_account: ProxyAccount | None
    plan: TransferPlan


class TransferService:
    """Перенос оставшегося оплаченного срока со старого аккаунта клиента на новый."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        events: EventLog,
        alerter: Alerter,
        admin_texts: Texts,
        clock: Clock,
        term_locks: KeyedLocks,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._events = events
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._clock = clock
        self._term_locks = term_locks

    async def preview(
        self, from_telegram_id: int, to_telegram_id: int, payment_id: str
    ) -> TransferPreview | TransferRefused:
        """Что будет после переноса. Ничего не меняет."""
        prepared = await self._prepare(from_telegram_id, to_telegram_id, payment_id=payment_id)
        if isinstance(prepared, TransferRefused):
            return prepared
        return _preview(prepared)

    async def transfer(
        self,
        admin_id: int,
        order_id: int,
        to_telegram_id: int,
        *,
        expected_from: datetime,
        expected_to: datetime | None,
    ) -> TransferDone | TransferRefused:
        """Перенести — после подтверждения, если сроки обоих аккаунтов не изменились.

        Откуда — клиент подтверждённого заказа `order_id` (так кнопка подтверждения
        укладывается в 64 байта Telegram).
        """
        from_telegram_id = await self.order_owner(order_id)
        if from_telegram_id is None:
            return TransferRefused(TransferRefusal.PAYMENT_NOT_FOUND)
        ids = await self._client_ids(from_telegram_id, to_telegram_id)
        if ids is None:
            refused = await self._prepare(from_telegram_id, to_telegram_id, order_id=order_id)
            if not isinstance(refused, TransferRefused):
                raise RuntimeError("клиент пропал и нашёлся снова")
            return refused
        async with AsyncExitStack() as stack:
            for client_id in sorted(set(ids)):
                await stack.enter_async_context(self._term_locks.hold(client_id))
            prepared = await self._prepare(from_telegram_id, to_telegram_id, order_id=order_id)
            if isinstance(prepared, TransferRefused):
                return prepared
            target_before = prepared.target_account
            if not same_second(prepared.source_account.expire_at, expected_from) or (
                not same_second(
                    None if target_before is None else target_before.expire_at, expected_to
                )
            ):
                return TransferRefused(TransferRefusal.CHANGED)
            target_account = await self._grant_target(prepared)
            await self._end_source(prepared, target_account)
            await self._record(admin_id, prepared, target_account)
        logger.info(
            "Администратор %s перенёс подписку клиента %s на клиента %s",
            admin_id,
            prepared.source.id,
            prepared.target.id,
        )
        return TransferDone(from_telegram_id, target_account.expire_at)

    async def order_owner(self, order_id: int) -> int | None:
        """Telegram ID клиента заказа. `None` — такого заказа нет."""
        async with self._session_factory() as session:
            return await session.scalar(
                select(Client.telegram_id).join(Order).where(Order.id == order_id)
            )

    async def _client_ids(self, from_id: int, to_id: int) -> tuple[int, int] | None:
        async with self._session_factory() as session:
            source = await _client_id(session, from_id)
            target = await _client_id(session, to_id)
        if source is None or target is None:
            return None
        return source, target

    async def _prepare(
        self,
        from_telegram_id: int,
        to_telegram_id: int,
        *,
        payment_id: str | None = None,
        order_id: int | None = None,
    ) -> _Prepared | TransferRefused:
        if from_telegram_id == to_telegram_id:
            return TransferRefused(TransferRefusal.SAME_CLIENT)
        async with self._session_factory() as session:
            source = await _client(session, from_telegram_id)
            if source is None:
                return TransferRefused(TransferRefusal.SOURCE_NOT_FOUND)
            target = await _client(session, to_telegram_id)
            if target is None:
                return TransferRefused(TransferRefusal.TARGET_NOT_FOUND)
            order = await _confirmed_order(session, source.id, payment_id, order_id)
            if order is None:
                return TransferRefused(TransferRefusal.PAYMENT_NOT_FOUND)
            if await _refund_open(session, source.id):
                return TransferRefused(TransferRefusal.REFUND_OPEN)
        source_account = await self._account(from_telegram_id)
        target_account = await self._account(to_telegram_id)
        now = utc_now(self._clock)
        plan = None
        if source_account is not None:
            plan = plan_transfer(
                client_snapshot(source, source_account), _snapshot(target, target_account), now
            )
        if source_account is None or plan is None:
            return TransferRefused(TransferRefusal.NOTHING_TO_TRANSFER)
        return _Prepared(source, target, order, source_account, target_account, plan)

    async def _grant_target(self, prepared: _Prepared) -> ProxyAccount:
        """Новому аккаунту — подписка до `target_expire_at`, как при оплате."""
        telegram_id = prepared.target.telegram_id
        target = prepared.plan.target_expire_at
        limits = paid_access_limits()
        account = prepared.target_account
        if account is None:
            account = await self._remnawave.get_or_create_account(
                telegram_id,
                expire_at=target,
                device_limit=limits.device_limit,
                traffic_limit_bytes=limits.traffic_limit_bytes,
            )
        if (account.device_limit, account.traffic_limit_bytes) != (
            limits.device_limit,
            limits.traffic_limit_bytes,
        ):
            account = await self._remnawave.set_limits(
                telegram_id,
                device_limit=limits.device_limit,
                traffic_limit_bytes=limits.traffic_limit_bytes,
            )
        if not same_second(account.expire_at, target):
            account = await self._remnawave.set_expiry(telegram_id, target)
        if account.status is AccountStatus.DISABLED:
            account = await self._remnawave.enable_account(telegram_id)
        async with self._session_factory() as session:
            await session.execute(
                update(Client).where(Client.id == prepared.target.id).values(grace_started_at=None)
            )
            await session.commit()
        return account

    async def _end_source(self, prepared: _Prepared, target_account: ProxyAccount) -> None:
        """Старому аккаунту — срок «сейчас» и выключенный доступ. Не вышло — тревога."""
        telegram_id = prepared.source.telegram_id
        try:
            await self._remnawave.set_expiry(telegram_id, prepared.plan.source_expire_at)
            await self._remnawave.disable_account(telegram_id)
        except RemnawaveError as error:
            logger.exception("Перенос: старый аккаунт клиента %s не отключён", prepared.source.id)
            await self._alerter.alert(
                TRANSFER_ALERT,
                source=self._end_source,
                error=error,
                glue=f"{TRANSFER_ALERT}:{prepared.source.id}",
                from_id=telegram_id,
                to_id=prepared.target.telegram_id,
                expire_at=format_moscow(target_account.expire_at),
            )
            raise

    async def _record(
        self, admin_id: int, prepared: _Prepared, target_account: ProxyAccount
    ) -> None:
        """`ADMIN_ACTION` у обоих клиентов — одной транзакцией."""
        details = {
            "action": AdminActionKind.SUBSCRIPTION_TRANSFERRED.value,
            "from_telegram_id": prepared.source.telegram_id,
            "to_telegram_id": prepared.target.telegram_id,
            "payment_id": prepared.order.payment_id,
            "left_seconds": int(prepared.plan.left.total_seconds()),
            "from_expire_before": prepared.source_account.expire_at.isoformat(),
            "to_expire_after": target_account.expire_at.isoformat(),
        }
        async with self._session_factory() as session:
            for client in (prepared.source, prepared.target):
                await self._events.record(
                    session,
                    EventKind.ADMIN_ACTION,
                    client_id=client.id,
                    order_id=prepared.order.id,
                    admin_id=admin_id,
                    details=details,
                )
            await session.commit()

    async def _account(self, telegram_id: int) -> ProxyAccount | None:
        try:
            return await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            return None  # нормальная ситуация: новый аккаунт ещё не брал ни пробного, ни подписки


def _preview(prepared: _Prepared) -> TransferPreview:
    payment_id = prepared.order.payment_id
    if payment_id is None:
        raise ValueError(f"заказ {prepared.order.id} выдан без номера платежа")
    target = prepared.target_account
    return TransferPreview(
        from_telegram_id=prepared.source.telegram_id,
        to_telegram_id=prepared.target.telegram_id,
        payment_id=payment_id,
        order_id=prepared.order.id,
        from_expire_at=prepared.source_account.expire_at,
        to_expire_at=None if target is None else target.expire_at,
        left=prepared.plan.left,
        new_expire_at=prepared.plan.target_expire_at,
    )


def _snapshot(client: Client, account: ProxyAccount | None) -> ClientSnapshot:
    if account is None:
        return ClientSnapshot(None, False, client.grace_started_at, access_disabled=False)
    return client_snapshot(client, account)


async def _client(session: AsyncSession, telegram_id: int) -> Client | None:
    return await session.scalar(select(Client).where(Client.telegram_id == telegram_id))


async def _client_id(session: AsyncSession, telegram_id: int) -> int | None:
    return await session.scalar(select(Client.id).where(Client.telegram_id == telegram_id))


async def _confirmed_order(
    session: AsyncSession, client_id: int, payment_id: str | None, order_id: int | None
) -> Order | None:
    """Оплаченный заказ клиента с выданным доступом — по номеру платежа или заказа."""
    statement = select(Order).where(
        Order.client_id == client_id, Order.status == OrderStatus.ACCESS_GRANTED
    )
    if payment_id is not None:
        statement = statement.where(Order.payment_id == payment_id)
    elif order_id is not None:
        statement = statement.where(Order.id == order_id)
    else:
        raise ValueError("нужен номер платежа или заказа")
    return await session.scalar(statement)


async def _refund_open(session: AsyncSession, client_id: int) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    RefundRequest.client_id == client_id, RefundRequest.status.in_(OPEN_STATUSES)
                )
            )
        )
    )
