"""Компенсация при сбое ноды (задача 20; спецификация, админка п. 7; аудит §7.7).

Путь:

1. **Предложение.** Нода восстановилась (`NodeMonitor` закрыл сбой) — в «🚨 Тревоги» уходит
   предложение с кнопкой «Компенсировать»: длительность простоя с учётом «мигания»
   (`domain.compensation.incident_of`) и сколько начислить (простой + 1 день).
2. **Запуск — один раз на инцидент.** Нажатие атомарно отмечает все ещё не компенсированные
   сбои инцидента (`claim_outage_compensation`) и пишет событие `ADMIN_ACTION` — это
   «прогон компенсации», его id — ключ для начислений клиентам. Часть инцидента уже
   компенсирована (нода «мигнула» после нажатия) — начисляется только новое
   (`remaining_compensation`). Всё уже отмечено — нажатие продолжает тот же прогон.
3. **Начисление клиентам — ровно один раз на клиента.** Только аккаунты бота (`list_accounts`
   отдаёт только их; 28 аккаунтов, заведённых вручную, не трогаются). Каждый клиент — под
   общей блокировкой срока (`term_locks`) и по аккаунту, перечитанному из панели. Сначала
   событие `COMPENSATION_GRANTED` (оно же отметка «этому клиенту по этому прогону уже
   начислено»), потом срок в панели «до X», потом в событии ставится `applied_at`.
   Упал посреди — повторное нажатие видит событие и по сроку в панели понимает, поставлен
   ли он (`resume_extension`). Срок изменился иначе — клиент в отчёте «на ручную проверку».
4. **Льготный период.** Срок в панели не трогаем, компенсация записывается как
   перенесённая и добавляется к сроку при следующей оплате (`carried_compensation`,
   его вызывает зачёт оплаты).
5. **Ход и итог** — сообщениями в «🚨 Тревоги»; клиентам — извинение.

Прогон идёт в фоне (`launch`): нажатие кнопки не ждёт сотни запросов к панели.
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, NodeOutage, Order
from tsproxy.db.repository import claim_outage_compensation
from tsproxy.domain.compensation import (
    NO_TIME,
    CompensationAction,
    Outage,
    ResumeAction,
    incident_of,
    plan_compensation,
    remaining_compensation,
    resume_extension,
)
from tsproxy.domain.dates import require_aware
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    ProxyAccount,
    RemnawaveClient,
    RemnawaveError,
)
from tsproxy.services.admin import AdminActionKind
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.grace import client_snapshot
from tsproxy.services.locks import KeyedLocks

logger = logging.getLogger(__name__)

# Ход прогона в «🚨 Тревоги» — после каждых стольких клиентов (значение выбрано агентом)
PROGRESS_EVERY = 25
# Панель могла отдать одинаковое время начала и конца сбоя — считаем такой сбой секундным
MIN_OUTAGE = timedelta(seconds=1)

# Поля подробностей событий. Прогон (`ADMIN_ACTION`):
ACTION = "action"
OUTAGE_IDS = "outage_ids"
AMOUNT_SECONDS = "amount_seconds"
INCIDENT_STARTED_AT = "incident_started_at"
INCIDENT_ENDED_AT = "incident_ended_at"
# Начисление клиенту (`COMPENSATION_GRANTED`): прогон, вид, срок до и после, когда поставлен
RUN_ID = "run_id"
EXPIRE_BEFORE = "expire_before"
EXTEND_TO = "extend_to"
APPLIED_AT = "applied_at"


class StartOutcome(StrEnum):
    STARTED = "started"  # новый прогон
    RESUMED = "resumed"  # инцидент уже отмечен — продолжаем тот же прогон
    NOT_FOUND = "not_found"
    STILL_OPEN = "still_open"  # нода ещё не восстановилась


class ClientOutcome(StrEnum):
    """Что вышло у одного клиента в прогоне."""

    EXTENDED = "extended"  # срок продлён
    CARRIED_OVER = "carried_over"  # льготный: добавится к следующей оплате
    ALREADY_COMPENSATED = "already_compensated"  # по этому прогону уже начислено
    NOT_ELIGIBLE = "not_eligible"  # нет оплаченной подписки (пробный, истёк, выключен)
    DEFERRED = "deferred"  # идёт выдача доступа по оплате — нажать ещё раз позже
    FAILED = "failed"  # панель не ответила или отказала — нажать ещё раз
    MANUAL = "manual"  # начисление записано, а срок изменился иначе — проверить вручную
    SKIPPED = "skipped"  # аккаунт бота без клиента в базе или аккаунт пропал


# Клиенты, которых следующее нажатие может доделать
UNFINISHED = frozenset({ClientOutcome.DEFERRED, ClientOutcome.FAILED})


@dataclass(frozen=True)
class CompensationOffer:
    """Предложение компенсировать: инцидент с этим сбоем и сколько ещё начислить."""

    outage_id: int
    incident: Outage
    amount: timedelta


@dataclass(frozen=True)
class CompensationRun:
    """Прогон компенсации: `run_id` — id события `ADMIN_ACTION`, ключ начислений клиентам."""

    run_id: int
    admin_id: int
    outage_id: int  # по кнопке какого сбоя запущен (для кнопки «Продолжить»)
    amount: timedelta
    incident: Outage


@dataclass(frozen=True)
class CompensationStart:
    outcome: StartOutcome
    run: CompensationRun | None = None


@dataclass(frozen=True)
class CompensationReport:
    """Итог прогона. `manual` — Telegram ID клиентов на ручную проверку."""

    run: CompensationRun
    total: int
    counts: Counter[ClientOutcome] = field(default_factory=Counter)
    manual: tuple[int, ...] = ()

    @property
    def unfinished(self) -> int:
        return sum(self.counts[outcome] for outcome in UNFINISHED)


@dataclass(frozen=True)
class Apology:
    """Извинение клиенту: `until` — новый срок (EXTEND), для CARRY_OVER — None."""

    action: CompensationAction
    amount: timedelta
    until: datetime | None = None


class CompensationReporter(Protocol):
    """Сообщения администраторам о компенсации (в боте — `bot/admin_compensation.py`).

    Сбой Telegram реализация пишет в лог и не бросает: начисление важнее отчёта.
    """

    async def offer(self, offer: CompensationOffer) -> None: ...

    async def started(self, run: CompensationRun, total: int) -> None: ...

    async def progress(self, run: CompensationRun, done: int, total: int) -> None: ...

    async def finished(self, report: CompensationReport) -> None: ...


class ApologyMessenger(Protocol):
    async def send_apology(self, telegram_id: int, apology: Apology) -> bool:
        """Прислать извинение. `False` — клиент заблокировал бота."""
        ...


class CompensationService:
    """Предложить, запустить и провести компенсацию за сбой ноды."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        events: EventLog,
        alerter: Alerter,
        admin_texts: Texts,
        reporter: CompensationReporter,
        messenger: ApologyMessenger,
        clock: Clock,
        term_locks: KeyedLocks,
        *,
        progress_every: int = PROGRESS_EVERY,
    ) -> None:
        if progress_every <= 0:
            raise ValueError("Шаг отчёта о ходе компенсации должен быть больше нуля")
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._events = events
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._reporter = reporter
        self._messenger = messenger
        self._clock = clock
        # Та же блокировка срока, что у зачёта оплаты, ручной выдачи и льготного (`term_guard`)
        self._term_locks = term_locks
        self._progress_every = progress_every
        # Два нажатия разом не должны оба отметить инцидент
        self._claim_lock = asyncio.Lock()
        self._running: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    # --- Предложение ---

    async def outage_closed(self, outage_id: int) -> None:
        """Нода восстановилась (`NodeMonitor`): предложить компенсацию, если есть что начислить."""
        offer = await self.describe(outage_id)
        if offer is None or offer.amount <= NO_TIME:
            logger.info("Сбой %s: компенсировать нечего (уже покрыт)", outage_id)
            return
        await self._reporter.offer(offer)

    async def describe(self, outage_id: int) -> CompensationOffer | None:
        """Что начислит нажатие. `None` — сбоя нет или нода ещё не восстановилась."""
        async with self._session_factory() as session:
            target, closed = await _load(session, outage_id)
        if target is None or target.ended_at is None:
            return None
        incident, members = _incident(target, closed)
        return CompensationOffer(outage_id, incident, _remaining(incident, members))

    # --- Запуск ---

    async def compensate(self, outage_id: int, admin_id: int) -> CompensationStart:
        """Отметить инцидент компенсированным (один раз) или найти уже начатый прогон."""
        async with self._claim_lock, self._session_factory() as session:
            target, closed = await _load(session, outage_id)
            if target is None:
                return CompensationStart(StartOutcome.NOT_FOUND)
            if target.ended_at is None:
                return CompensationStart(StartOutcome.STILL_OPEN)
            incident, members = _incident(target, closed)
            fresh = [member for member in members if member.compensated_at is None]
            if not fresh:
                return CompensationStart(
                    StartOutcome.RESUMED, await _find_run(session, outage_id, incident)
                )
            amount = _remaining(incident, members)
            now = utc_now(self._clock)
            for member in fresh:
                if not await claim_outage_compensation(session, member.id, admin_id, now):
                    # Отметку поставил кто-то в обход `_claim_lock` (второй процесс бота?)
                    await session.rollback()
                    raise RuntimeError(f"сбой {member.id} компенсирован параллельно")
            event = await self._events.record(
                session,
                EventKind.ADMIN_ACTION,
                admin_id=admin_id,
                details={
                    ACTION: AdminActionKind.OUTAGE_COMPENSATION,
                    OUTAGE_IDS: sorted(member.id for member in fresh),
                    AMOUNT_SECONDS: int(amount.total_seconds()),
                    INCIDENT_STARTED_AT: incident.started_at,
                    INCIDENT_ENDED_AT: incident.ended_at,
                },
            )
            run = CompensationRun(event.id, admin_id, outage_id, amount, incident)
            await session.commit()
        logger.info("Компенсация #%s: по сбою %s начисляем %s", run.run_id, outage_id, amount)
        return CompensationStart(StartOutcome.STARTED, run)

    def launch(self, run: CompensationRun) -> asyncio.Task[None] | None:
        """Провести прогон в фоне. `None` — этот прогон уже идёт (второе нажатие)."""
        if run.run_id in self._running:
            return None
        self._running.add(run.run_id)
        task = asyncio.create_task(self._run_in_background(run), name=f"compensation:{run.run_id}")
        self._tasks.add(task)  # иначе задачу может собрать сборщик мусора
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Дождаться всех фоновых прогонов (тесты; ошибки прогонов уже ушли в тревоги)."""
        while self._tasks:
            await asyncio.gather(*self._tasks)

    async def _run_in_background(self, run: CompensationRun) -> None:
        try:
            await self.run(run)
        # Фоновая задача: ошибку некому поймать. Пишем в лог и поднимаем тревогу —
        # повторное нажатие продолжит с того же места, дважды никому не начислит
        except Exception as error:
            logger.exception("Компенсация #%s прервалась", run.run_id)
            await self._alerter.raise_alert(
                f"compensation_failed:{run.run_id}",
                self._admin_texts.get(
                    "alerts.compensation_failed", run_id=run.run_id, error=type(error).__name__
                ),
            )
        finally:
            self._running.discard(run.run_id)

    # --- Прогон ---

    async def run(self, run: CompensationRun) -> CompensationReport:
        """Начислить всем клиентам бота с оплаченной подпиской. Повторный вызов безопасен.

        Ошибку панели при чтении списка аккаунтов пробрасывает (прогон не начался).
        """
        if run.amount <= NO_TIME:
            report = CompensationReport(run, total=0)
            await self._reporter.finished(report)
            return report
        telegram_ids = await self._candidates()
        total = len(telegram_ids)
        await self._reporter.started(run, total)
        counts: Counter[ClientOutcome] = Counter()
        manual: list[int] = []
        for done, telegram_id in enumerate(telegram_ids, start=1):
            outcome = await self._client(run, telegram_id)
            counts[outcome] += 1
            if outcome is ClientOutcome.MANUAL:
                manual.append(telegram_id)
            if done % self._progress_every == 0 and done < total:
                await self._reporter.progress(run, done, total)
        report = CompensationReport(run, total, counts, tuple(manual))
        logger.info("Компенсация #%s закончена: %s", run.run_id, dict(counts))
        await self._reporter.finished(report)
        return report

    async def _candidates(self) -> list[int]:
        """Клиенты бота, у которых срок в панели ещё идёт. Чужие аккаунты обёртка не отдаёт."""
        now = utc_now(self._clock)
        accounts = await self._remnawave.list_accounts()
        telegram_ids = set()
        for account in accounts:
            if account.telegram_id is None:
                logger.warning("Компенсация: у аккаунта бота %s нет Telegram ID", account.id)
                continue
            if account.expire_at > now:
                telegram_ids.add(account.telegram_id)
        return sorted(telegram_ids)

    async def _client(self, run: CompensationRun, telegram_id: int) -> ClientOutcome:
        try:
            return await self._compensate_client(run, telegram_id)
        except RemnawaveError as error:
            # Начисление записано (если дошло до записи) — следующее нажатие доделает
            logger.warning(
                "Компенсация #%s: клиент %s не обработан: %s", run.run_id, telegram_id, error
            )
            return ClientOutcome.FAILED

    async def _compensate_client(self, run: CompensationRun, telegram_id: int) -> ClientOutcome:
        async with self._session_factory() as session:
            client_id = await session.scalar(
                select(Client.id).where(Client.telegram_id == telegram_id)
            )
        if client_id is None:
            logger.warning(
                "Компенсация: аккаунт клиента %s есть в панели, а в базе нет", telegram_id
            )
            return ClientOutcome.SKIPPED
        async with self._term_locks.hold(client_id):
            # всё — заново под блокировкой: оплата или льготный могли поменять срок
            async with self._session_factory() as session:
                client = await session.get_one(Client, client_id)
                claim = await _find_claim(session, client_id, run.run_id)
                granting = await _grant_pending(session, client_id)
            try:
                account = await self._remnawave.get_account(telegram_id)
            except AccountNotFound:
                logger.warning("Компенсация: аккаунт клиента %s пропал из панели", telegram_id)
                return ClientOutcome.SKIPPED
            if claim is not None:
                return await self._resume(client, account, claim)
            if granting:
                # Срок ещё пересчитает выдача по оплате и затрёт продление — подождём её
                return ClientOutcome.DEFERRED
            now = utc_now(self._clock)
            plan = plan_compensation(
                client_snapshot(client, account), run.amount, already_compensated=False, now=now
            )
            if plan.action is CompensationAction.EXTEND and plan.extend_to is not None:
                return await self._extend(run, client, account, plan.extend_to)
            if plan.action is CompensationAction.CARRY_OVER:
                return await self._carry_over(run, client)
            return ClientOutcome.NOT_ELIGIBLE

    async def _extend(
        self, run: CompensationRun, client: Client, account: ProxyAccount, target: datetime
    ) -> ClientOutcome:
        """Сначала отметка в журнале, потом срок в панели — так дважды не начислится."""
        async with self._session_factory() as session:
            event = await self._record_grant(
                session,
                run,
                client.id,
                CompensationAction.EXTEND,
                {EXPIRE_BEFORE: account.expire_at, EXTEND_TO: target, APPLIED_AT: None},
            )
            event_id = event.id
            await session.commit()
        await self._apply(client, event_id, target, run.amount)
        return ClientOutcome.EXTENDED

    async def _carry_over(self, run: CompensationRun, client: Client) -> ClientOutcome:
        """Льготный: срок не трогаем, событие — это и есть перенесённая компенсация."""
        async with self._session_factory() as session:
            await self._record_grant(session, run, client.id, CompensationAction.CARRY_OVER, {})
            await session.commit()
        logger.info("Компенсация #%s: клиенту %s — к следующей оплате", run.run_id, client.id)
        await self._apologize(client, Apology(CompensationAction.CARRY_OVER, run.amount))
        return ClientOutcome.CARRIED_OVER

    async def _resume(self, client: Client, account: ProxyAccount, claim: Event) -> ClientOutcome:
        """Начисление по прогону уже записано: доделать, если срок в панели не поставлен."""
        details = claim.details or {}
        if details.get(ACTION) != CompensationAction.EXTEND or details.get(APPLIED_AT) is not None:
            return ClientOutcome.ALREADY_COMPENSATED
        before = _moment(details[EXPIRE_BEFORE])
        target = _moment(details[EXTEND_TO])
        amount = timedelta(seconds=details[AMOUNT_SECONDS])
        match resume_extension(account.expire_at, before, target):
            case ResumeAction.APPLY:
                await self._apply(client, claim.id, target, amount)
            case ResumeAction.ALREADY_APPLIED:
                # панель срок поставила, а ответ или отметка потерялись
                await self._mark_applied(claim.id)
                await self._apologize(client, Apology(CompensationAction.EXTEND, amount, target))
            case ResumeAction.UNCLEAR:
                logger.warning(
                    "Компенсация: клиент %s — срок %s, ждали %s или %s; проверить вручную",
                    client.telegram_id,
                    account.expire_at.isoformat(),
                    before.isoformat(),
                    target.isoformat(),
                )
                return ClientOutcome.MANUAL
        return ClientOutcome.EXTENDED

    async def _apply(
        self, client: Client, event_id: int, target: datetime, amount: timedelta
    ) -> None:
        await self._remnawave.set_expiry(client.telegram_id, target)
        await self._mark_applied(event_id)
        logger.info("Компенсация: клиенту %s срок до %s", client.telegram_id, target.isoformat())
        await self._apologize(client, Apology(CompensationAction.EXTEND, amount, target))

    async def _record_grant(
        self,
        session: AsyncSession,
        run: CompensationRun,
        client_id: int,
        action: CompensationAction,
        extra: dict[str, Any],
    ) -> Event:
        return await self._events.record(
            session,
            EventKind.COMPENSATION_GRANTED,
            client_id=client_id,
            admin_id=run.admin_id,
            details={
                RUN_ID: run.run_id,
                ACTION: action,
                AMOUNT_SECONDS: int(run.amount.total_seconds()),
                **extra,
            },
        )

    async def _mark_applied(self, event_id: int) -> None:
        async with self._session_factory() as session:
            event = await session.get_one(Event, event_id)
            # новый словарь, а не правка старого: иначе SQLAlchemy не заметит изменения JSON
            event.details = {**(event.details or {}), APPLIED_AT: utc_now(self._clock).isoformat()}
            await session.commit()

    async def _apologize(self, client: Client, apology: Apology) -> None:
        if client.bot_blocked_at is not None:
            return
        try:
            delivered = await self._messenger.send_apology(client.telegram_id, apology)
        # Компенсация уже начислена — сбой Telegram не должен остановить прогон для
        # остальных. Не прячем: в лог с трассировкой
        except Exception:
            logger.exception("Компенсация: извинение клиенту %s не ушло", client.telegram_id)
            return
        if not delivered:
            logger.info("Клиент %s заблокировал бота: извинение не ушло", client.telegram_id)


# --- Перенесённая компенсация (её учитывает зачёт оплаты) ---


async def carried_compensation(
    session: AsyncSession, client_id: int, grace_started_at: datetime | None
) -> timedelta:
    """Компенсация, начисленная клиенту в льготный период, начатый в `grace_started_at`.

    Это события CARRY_OVER после отметки «льготный начался». Следующий льготный начнётся
    позже этих событий, поэтому одна и та же компенсация к двум оплатам не добавится.
    """
    if grace_started_at is None:
        return NO_TIME
    details = await session.scalars(
        select(Event.details).where(
            Event.kind == EventKind.COMPENSATION_GRANTED.value,
            Event.client_id == client_id,
            Event.occurred_at >= grace_started_at,
        )
    )
    return sum(
        (
            timedelta(seconds=item[AMOUNT_SECONDS])
            for item in details
            if item is not None and item.get(ACTION) == CompensationAction.CARRY_OVER
        ),
        NO_TIME,
    )


# --- Чтение из базы ---


async def _load(
    session: AsyncSession, outage_id: int
) -> tuple[NodeOutage | None, list[NodeOutage]]:
    """Сбой по id и все закончившиеся сбои (их немного: единицы в месяц)."""
    target = await session.get(NodeOutage, outage_id)
    closed = await session.scalars(select(NodeOutage).where(NodeOutage.ended_at.is_not(None)))
    return target, list(closed)


def _as_outage(row: NodeOutage) -> Outage:
    if row.ended_at is None:
        raise ValueError(f"сбой {row.id} ещё идёт")
    return Outage(row.started_at, max(row.ended_at, row.started_at + MIN_OUTAGE))


def _incident(target: NodeOutage, closed: Sequence[NodeOutage]) -> tuple[Outage, list[NodeOutage]]:
    """Инцидент со сбоем `target` и все сбои, которые в него входят."""
    incident = incident_of((_as_outage(row) for row in closed), _as_outage(target))
    members = [row for row in closed if _within(_as_outage(row), incident)]
    return incident, members


def _within(outage: Outage, incident: Outage) -> bool:
    return incident.started_at <= outage.started_at and outage.ended_at <= incident.ended_at


def _remaining(incident: Outage, members: Iterable[NodeOutage]) -> timedelta:
    compensated = [_as_outage(row) for row in members if row.compensated_at is not None]
    return remaining_compensation(incident, compensated)


async def _find_run(session: AsyncSession, outage_id: int, incident: Outage) -> CompensationRun:
    """Прогон, которым компенсирован сбой `outage_id`."""
    events = await session.scalars(
        select(Event)
        .where(
            Event.kind == EventKind.ADMIN_ACTION.value,
            Event.details[ACTION].as_string() == AdminActionKind.OUTAGE_COMPENSATION.value,
        )
        .order_by(Event.id)
    )
    for event in events:
        details = event.details or {}
        if outage_id in details.get(OUTAGE_IDS, []):
            if event.admin_id is None:
                raise ValueError(f"прогон компенсации {event.id} без администратора")
            amount = timedelta(seconds=details[AMOUNT_SECONDS])
            return CompensationRun(event.id, event.admin_id, outage_id, amount, incident)
    raise LookupError(f"сбой {outage_id} отмечен компенсированным, а прогона в журнале нет")


async def _find_claim(session: AsyncSession, client_id: int, run_id: int) -> Event | None:
    """Начисление клиенту по прогону `run_id`, если уже записано."""
    events = await session.scalars(
        select(Event).where(
            Event.kind == EventKind.COMPENSATION_GRANTED.value, Event.client_id == client_id
        )
    )
    for event in events:
        if event.details is not None and event.details.get(RUN_ID) == run_id:
            return event
    return None


async def _grant_pending(session: AsyncSession, client_id: int) -> bool:
    """Есть оплата, по которой доступ ещё выдаётся (срок пересчитается)."""
    return bool(
        await session.scalar(
            select(exists().where(Order.client_id == client_id, Order.status == OrderStatus.PAID))
        )
    )


def _moment(raw: str) -> datetime:
    return require_aware(datetime.fromisoformat(raw))
