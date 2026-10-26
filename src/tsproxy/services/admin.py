"""Админка (задача 18): поиск клиента, карточка и действия администратора.

Сервис ничего не знает про Telegram: кто администратор и где можно действовать, проверяет
бот (`bot.admin.AdminFilter`) на каждом обработчике. Здесь — что именно делается и запись
каждого действия в журнал (`ADMIN_ACTION` с Telegram ID администратора).

Ошибки администраторов (аудит R22):
- действие — только по Telegram ID и только для клиентов бота (есть в базе): по чужому ID
  прокси-аккаунт не создаётся; аккаунты не `tg_` не трогает обёртка Remnawave;
- ручная выдача — не больше `MAX_MANUAL_GRANT_DAYS` за раз и с подтверждением: кнопка
  «Да» несёт срок, который видел администратор (`expected_expire_at`). Срок уже другой
  (двойное нажатие, второй администратор, оплата) — выдача не делается, `CHANGED`.

Методы — точка входа и для кнопок карточки клиента в теме поддержки (задача 17).
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Order
from tsproxy.db.repository import reset_trial_claim
from tsproxy.domain.admin import ClientQuery, check_grant_days, manual_grant_target
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.trial import paid_access_limits
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    AccountStatus,
    ProxyAccount,
    RemnawaveClient,
    RemnawaveUnavailable,
)
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.grace import client_snapshot
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.profile import DeviceDeletion, DeviceList, Profile, ProfileService
from tsproxy.services.trial import has_paid_order, trial_delivered

logger = logging.getLogger(__name__)

# Сколько последних оплат показывать в карточке; всего оплат и сумма — по всем
CARD_RECENT_PAYMENTS = 5
# Больше совпадений по @username не показываем: администратору нужен Telegram ID
SEARCH_LIMIT = 10


class AdminActionKind(StrEnum):
    """Что сделал администратор — поле `action` в подробностях события `ADMIN_ACTION`."""

    EXTEND = "extend"  # ручная выдача или продление на N дней
    TRIAL_RESET = "trial_reset"  # сброс пробного периода
    DEVICE_DELETED = "device_deleted"  # удалено устройство клиента
    LINK_REVOKED = "link_revoked"  # перевыпущена ссылка подписки
    REFUND_APPROVED = "refund_approved"  # одобрен возврат (задача 21)
    REFUND_REJECTED = "refund_rejected"  # отклонён возврат (задача 21)
    SUBSCRIPTION_TRANSFERRED = "subscription_transferred"  # перенос подписки (задача 21)


class ExtensionOutcome(StrEnum):
    EXTENDED = "extended"
    CHANGED = "changed"  # срок уже не тот, что видел администратор: ничего не сделано
    CLIENT_NOT_FOUND = "client_not_found"  # не клиент бота


class TrialResetOutcome(StrEnum):
    RESET = "reset"
    NOT_USED = "not_used"  # пробный не брал (или отметку только что сняли)
    HAS_PAID = "has_paid"  # клиент платил: пробный ему не положен
    CLIENT_NOT_FOUND = "client_not_found"


class RevokeOutcome(StrEnum):
    REVOKED = "revoked"
    NO_ACCOUNT = "no_account"  # прокси-аккаунта нет — перевыпускать нечего
    CLIENT_NOT_FOUND = "client_not_found"


@dataclass(frozen=True)
class TrialInfo:
    """Пробный: когда отмечен, доставлен ли (событие после отметки), сколько продлений."""

    granted_at: datetime | None
    delivered: bool
    extensions: int


@dataclass(frozen=True)
class PaymentInfo:
    order_id: int
    tariff_id: str
    days: int
    amount: Decimal
    paid_at: datetime
    status: OrderStatus


@dataclass(frozen=True)
class PaymentsSummary:
    """Оплаченные заказы: сколько, на какую сумму и последние (новые сверху)."""

    count: int
    total: Decimal
    recent: tuple[PaymentInfo, ...]


@dataclass(frozen=True)
class PanelInfo:
    """Что знает Remnawave. `profile`/`devices` — None, если прокси-аккаунта нет."""

    profile: Profile | None
    devices: DeviceList | None


@dataclass(frozen=True)
class ClientCard:
    """Карточка клиента. `panel` — None, если панель не ответила: база всё равно видна."""

    client: Client
    trial: TrialInfo
    payments: PaymentsSummary
    panel: PanelInfo | None


@dataclass(frozen=True)
class ExtensionPreview:
    """Что будет после выдачи: срок сейчас (None — аккаунта нет) и новый срок."""

    telegram_id: int
    days: int
    current_expire_at: datetime | None
    new_expire_at: datetime


@dataclass(frozen=True)
class ExtensionResult:
    """`expire_at` — новый срок (EXTENDED) или нынешний (CHANGED)."""

    outcome: ExtensionOutcome
    expire_at: datetime | None = None


def same_second(first: datetime | None, second: datetime | None) -> bool:
    """Сроки совпадают с точностью до секунды (в кнопке — секунды, в панели — миллисекунды)."""
    if first is None or second is None:
        return first is second
    return int(first.timestamp()) == int(second.timestamp())


class AdminService:
    """Поиск, карточка и действия администратора над клиентом бота (по Telegram ID)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        profiles: ProfileService,
        events: EventLog,
        clock: Clock,
        term_locks: KeyedLocks,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._profiles = profiles
        self._events = events
        self._clock = clock
        # Та же блокировка по id клиента, что у зачёта оплаты (`CreditingService`): иначе
        # оплата и ручная выдача посчитают новый срок от одной старой даты и дни потеряются
        self._term_locks = term_locks

    # --- Поиск и карточка ---

    async def find_clients(self, query: ClientQuery) -> list[Client]:
        """Клиенты бота по Telegram ID или @username (без учёта регистра)."""
        statement = select(Client).order_by(Client.id).limit(SEARCH_LIMIT)
        if query.telegram_id is not None:
            statement = statement.where(Client.telegram_id == query.telegram_id)
        elif query.username is not None:
            statement = statement.where(func.lower(Client.username) == query.username.lower())
        else:
            raise ValueError("Пустой запрос поиска клиента")
        async with self._session_factory() as session:
            return list(await session.scalars(statement))

    async def card(self, telegram_id: int) -> ClientCard | None:
        """Карточка клиента. `None` — такого клиента у бота нет."""
        async with self._session_factory() as session:
            client = await _client(session, telegram_id)
            if client is None:
                return None
            trial = await _trial_info(session, client)
            payments = await _payments(session, client.id)
        return ClientCard(client, trial, payments, await self._panel_info(client))

    async def _panel_info(self, client: Client) -> PanelInfo | None:
        try:
            profile = await self._profiles.profile(client)
            devices = await self._profiles.devices(client.telegram_id)
        except RemnawaveUnavailable as error:
            # Карточка нужна и без панели: оплаты и пробный — из базы; админ видит «не ответила»
            logger.warning("Карточка клиента %s: панель не ответила: %s", client.id, error)
            return None
        return PanelInfo(profile, devices)

    # --- Ручная выдача ---

    async def preview_extension(self, telegram_id: int, days: int) -> ExtensionPreview | None:
        """До какой даты продлит выдача на `days` дней. Ничего не меняет.

        `None` — не клиент бота. Дней вне предела — `GrantDaysOutOfRange`.
        """
        check_grant_days(days)
        client = await self._find(telegram_id)
        if client is None:
            return None
        account = await self._account(telegram_id)
        target = manual_grant_target(_snapshot(client, account), self._now(), days)
        current = None if account is None else account.expire_at
        return ExtensionPreview(telegram_id, days, current, target)

    async def extend(
        self,
        admin_id: int,
        telegram_id: int,
        days: int,
        *,
        expected_expire_at: datetime | None,
    ) -> ExtensionResult:
        """Выдать или продлить подписку на `days` дней — после подтверждения.

        `expected_expire_at` — срок из предпросмотра (None — аккаунта не было). Выдача —
        как подписка: лимиты платного тарифа (пробный лимит 7 ГБ снимается), доступ
        включается, отметка льготного снимается, как при оплате.
        """
        check_grant_days(days)
        found = await self._find(telegram_id)
        if found is None:
            return ExtensionResult(ExtensionOutcome.CLIENT_NOT_FOUND)
        async with self._term_locks.hold(found.id):
            # перечитываем под блокировкой: зачёт оплаты мог снять отметку льготного
            client = await self._find(telegram_id)
            if client is None:
                return ExtensionResult(ExtensionOutcome.CLIENT_NOT_FOUND)
            account = await self._account(telegram_id)
            before = None if account is None else account.expire_at
            if not same_second(before, expected_expire_at):
                logger.info("Ручная выдача клиенту %s: срок уже изменился", client.id)
                return ExtensionResult(ExtensionOutcome.CHANGED, before)
            target = manual_grant_target(_snapshot(client, account), self._now(), days)
            account = await self._grant(telegram_id, account, target)
            async with self._session_factory() as session:
                await session.execute(
                    update(Client).where(Client.id == client.id).values(grace_started_at=None)
                )
                await self._record(
                    session,
                    admin_id,
                    client.id,
                    AdminActionKind.EXTEND,
                    days=days,
                    expire_before=None if before is None else before.isoformat(),
                    expire_after=account.expire_at.isoformat(),
                )
                await session.commit()
        logger.info("Администратор %s продлил клиента %s на %s дн.", admin_id, client.id, days)
        return ExtensionResult(ExtensionOutcome.EXTENDED, account.expire_at)

    async def _grant(
        self, telegram_id: int, account: ProxyAccount | None, target: datetime
    ) -> ProxyAccount:
        """Привести аккаунт к «подписка до `target`». Все шаги — абсолютные значения."""
        limits = paid_access_limits()
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
        if account.expire_at != target:
            account = await self._remnawave.set_expiry(telegram_id, target)
        if account.status is AccountStatus.DISABLED:
            account = await self._remnawave.enable_account(telegram_id)
        return account

    # --- Пробный ---

    async def reset_trial(self, admin_id: int, telegram_id: int) -> TrialResetOutcome:
        """Сбросить пробный: клиент снова видит «🎁 Попробовать бесплатно».

        Прокси-аккаунт не трогаем: новое нажатие обновит его (`TrialService`).
        """
        async with self._session_factory() as session:
            client = await _client(session, telegram_id)
            if client is None:
                return TrialResetOutcome.CLIENT_NOT_FOUND
            if await has_paid_order(session, client.id):
                return TrialResetOutcome.HAS_PAID
            granted_at = client.trial_granted_at
            if granted_at is None or not await reset_trial_claim(session, client.id, granted_at):
                return TrialResetOutcome.NOT_USED
            await self._record(
                session,
                admin_id,
                client.id,
                AdminActionKind.TRIAL_RESET,
                trial_granted_at=granted_at.isoformat(),
            )
            await session.commit()
        logger.info("Администратор %s сбросил пробный клиента %s", admin_id, client.id)
        return TrialResetOutcome.RESET

    # --- Устройства ---

    async def devices(self, telegram_id: int) -> DeviceList | None:
        """Устройства клиента. `None` — прокси-аккаунта нет."""
        return await self._profiles.devices(telegram_id)

    async def delete_device(
        self, admin_id: int, telegram_id: int, ref: str
    ) -> DeviceDeletion | None:
        """Удалить устройство из списка этого клиента (по ссылке из кнопки).

        `None` — не клиент бота или прокси-аккаунта нет.
        """
        client = await self._find(telegram_id)
        if client is None:
            return None
        result = await self._profiles.delete_device(telegram_id, ref)
        if result is None or not result.deleted:
            return result
        async with self._session_factory() as session:
            await self._record(
                session, admin_id, client.id, AdminActionKind.DEVICE_DELETED, device=ref
            )
            await session.commit()
        return result

    # --- Ссылка подписки ---

    async def revoke_link(self, admin_id: int, telegram_id: int) -> RevokeOutcome:
        """Перевыпустить ссылку подписки: старая перестаёт работать на всех устройствах."""
        client = await self._find(telegram_id)
        if client is None:
            return RevokeOutcome.CLIENT_NOT_FOUND
        try:
            await self._remnawave.revoke_subscription(telegram_id)
        except AccountNotFound:
            return RevokeOutcome.NO_ACCOUNT
        async with self._session_factory() as session:
            await self._record(session, admin_id, client.id, AdminActionKind.LINK_REVOKED)
            await session.commit()
        logger.info("Администратор %s перевыпустил ссылку клиента %s", admin_id, client.id)
        return RevokeOutcome.REVOKED

    # --- Внутреннее ---

    def _now(self) -> datetime:
        return utc_now(self._clock)

    async def _find(self, telegram_id: int) -> Client | None:
        async with self._session_factory() as session:
            return await _client(session, telegram_id)

    async def _account(self, telegram_id: int) -> ProxyAccount | None:
        try:
            return await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            return None  # нормальная ситуация: пробный не брал и не покупал

    async def _record(
        self,
        session: AsyncSession,
        admin_id: int,
        client_id: int,
        action: AdminActionKind,
        **details: object,
    ) -> None:
        await self._events.record(
            session,
            EventKind.ADMIN_ACTION,
            client_id=client_id,
            admin_id=admin_id,
            details={"action": action.value, **details},
        )


def _snapshot(client: Client, account: ProxyAccount | None) -> ClientSnapshot:
    """Состояние клиента для правил сроков; без прокси-аккаунта — «новый клиент»."""
    if account is None:
        return ClientSnapshot(None, False, client.grace_started_at, access_disabled=False)
    return client_snapshot(client, account)


async def _client(session: AsyncSession, telegram_id: int) -> Client | None:
    return await session.scalar(select(Client).where(Client.telegram_id == telegram_id))


async def _trial_info(session: AsyncSession, client: Client) -> TrialInfo:
    granted_at = client.trial_granted_at
    delivered = granted_at is not None and await trial_delivered(session, client.id, granted_at)
    return TrialInfo(granted_at, delivered, client.trial_extensions)


async def _payments(session: AsyncSession, client_id: int) -> PaymentsSummary:
    paid = list(
        await session.scalars(
            select(Order)
            .where(Order.client_id == client_id, Order.paid_at.is_not(None))
            .order_by(Order.paid_at.desc(), Order.id.desc())
        )
    )
    infos = [
        PaymentInfo(
            order_id=order.id,
            tariff_id=order.tariff_id,
            days=order.days,
            amount=order.paid_amount if order.paid_amount is not None else order.price,
            paid_at=order.paid_at,
            status=order.status,
        )
        for order in paid
        if order.paid_at is not None
    ]
    total = sum((info.amount for info in infos), Decimal(0))
    return PaymentsSummary(len(infos), total, tuple(infos[:CARD_RECENT_PAYMENTS]))
