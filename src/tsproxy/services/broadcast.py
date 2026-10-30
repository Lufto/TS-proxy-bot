"""Рассылка всем клиентам бота (задачи 19 и 32, спецификация «Админка» п. 5).

Что: текст (`/broadcast <текст>`) или копия сообщения из группы администраторов —
фото, видео, файл, голосовое… с подписью и разметкой (`copyMessage`, без «переслано от»).

Кому: клиентам бота из базы (`clients`), кроме заблокировавших бота. 28 нынешних клиентов
панели в базе бота не записаны — им рассылка не уходит; Remnawave рассылка не трогает вовсе.

Как защищаемся от ошибок:
- только после подтверждения: администратор видит предпросмотр и жмёт «✅ Отправить»;
- один предпросмотр — одна рассылка: запуск записывается в журнал (`ADMIN_ACTION`,
  `action=broadcast`, номер сообщения предпросмотра) под замком, повторное нажатие
  или второй администратор находят запись и ничего не шлют. Отменённый предпросмотр
  (`broadcast_cancelled`) тоже не отправить. Запись в базе переживает перезапуск бота;
  в ней же — что отправлено: текст или откуда копия (чат и номер сообщения);
- одна рассылка за раз: пока идёт одна, вторая не начинается (BUSY);
- темп: не больше `BROADCAST_MESSAGES_PER_SECOND` сообщений в секунду — с запасом
  от предела Telegram (≈ 30 в секунду на бота), чтобы оставалось место ответам клиентам.
  «Подождите N секунд» от Telegram — ждём и повторяем того же клиента;
- много неудач подряд (Telegram не отвечает) — рассылка останавливается, а не перебирает
  всех впустую.

Сервис ничего не знает про Telegram: отправку делает `BroadcastSender`, отчёты в тему
администраторов — `BroadcastReporter` (реализации — `bot.admin_broadcast`).
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import Client, Event
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)

# Предел Telegram — около 30 сообщений в секунду на бота; берём меньше, чтобы бот
# продолжал отвечать клиентам и поддержке, пока идёт рассылка
BROADCAST_MESSAGES_PER_SECOND = 20
BROADCAST_SEND_INTERVAL = timedelta(seconds=1 / BROADCAST_MESSAGES_PER_SECOND)
# Сколько раз ждать «подождите N секунд» по одному клиенту, прежде чем считать его неудачей
MAX_THROTTLE_RETRIES = 3
# Столько неудач подряд — Telegram или сеть лежат: останавливаемся
MAX_CONSECUTIVE_FAILURES = 20
# Промежуточный отчёт в тему — каждые столько клиентов
PROGRESS_EVERY = 500


class BroadcastActionKind(StrEnum):
    """Поле `action` в подробностях события `ADMIN_ACTION`."""

    BROADCAST = "broadcast"  # рассылка запущена
    CANCELLED = "broadcast_cancelled"  # предпросмотр отменён
    STOPPED = "broadcast_stopped"  # администратор остановил идущую рассылку


# Предпросмотр с такими записями уже использован: отправлен или отменён
HANDLED_PREVIEW_ACTIONS = (BroadcastActionKind.BROADCAST, BroadcastActionKind.CANCELLED)


class DeliveryOutcome(StrEnum):
    DELIVERED = "delivered"
    BLOCKED = "blocked"  # клиент заблокировал бота — пометку ставит отправитель
    FAILED = "failed"  # не доставлено по другой причине (в лог пишет отправитель)


class BroadcastThrottled(Exception):
    """Telegram просит подождать `retry_after` секунд перед следующим сообщением."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Telegram просит подождать {retry_after} с")
        self.retry_after = retry_after


@dataclass(frozen=True)
class BroadcastText:
    """Текст рассылки в HTML — ровно как в предпросмотре (`/broadcast <текст>`)."""

    html: str


@dataclass(frozen=True)
class BroadcastCopy:
    """Рассылка копией сообщения (`copyMessage`): фото, видео, файл… с подписью.

    Откуда копировать — сообщение предпросмотра в группе администраторов: его прислал бот,
    администраторы его не изменят, и клиенты получат ровно то, что видели в предпросмотре.
    """

    chat_id: int
    message_id: int


type BroadcastContent = BroadcastText | BroadcastCopy


class BroadcastSender(Protocol):
    async def send(self, telegram_id: int, content: BroadcastContent) -> DeliveryOutcome:
        """Отправить рассылку клиенту. «Подождите» — `BroadcastThrottled`."""
        ...


@dataclass(frozen=True)
class BroadcastProgress:
    total: int
    delivered: int = 0
    blocked: int = 0
    failed: int = 0

    @property
    def processed(self) -> int:
        return self.delivered + self.blocked + self.failed


class BroadcastEnd(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"  # остановил администратор или перезапуск бота
    ABORTED = "aborted"  # слишком много неудач подряд
    CRASHED = "crashed"  # неожиданная ошибка (подробности — в логе)


@dataclass(frozen=True)
class BroadcastReport:
    end: BroadcastEnd
    progress: BroadcastProgress
    started_at: datetime
    finished_at: datetime
    error: str | None = None


class BroadcastReporter(Protocol):
    """Сообщения о ходе рассылки в тему администраторов. Сбой отправки не бросает."""

    async def started(self, total: int) -> None: ...

    async def progress(self, progress: BroadcastProgress) -> None: ...

    async def finished(self, report: BroadcastReport) -> None: ...


@dataclass(frozen=True)
class PreviewRef:
    """Сообщение предпросмотра в группе администраторов, под которым нажали «Отправить»."""

    chat_id: int
    message_id: int


@dataclass(frozen=True)
class Audience:
    recipients: int
    blocked: int  # заблокировали бота — рассылка им не уйдёт


class StartOutcome(StrEnum):
    STARTED = "started"
    ALREADY_HANDLED = "already_handled"  # этот предпросмотр уже отправлен или отменён
    BUSY = "busy"  # идёт другая рассылка
    NO_RECIPIENTS = "no_recipients"


@dataclass(frozen=True)
class BroadcastStart:
    outcome: StartOutcome
    recipients: int = 0


class BroadcastService:
    """Запуск, отмена и остановка рассылки; сама рассылка — фоновая задача."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        events: EventLog,
        clock: Clock,
        sender: BroadcastSender,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._events = events
        self._clock = clock
        self._sender = sender
        self._sleep = sleep
        # Проверка «предпросмотр не использован» и запись о запуске — под одним замком:
        # два нажатия одновременно не запустят две рассылки
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def audience(self) -> Audience:
        """Сколько клиентов получат рассылку сейчас и сколько заблокировали бота."""
        async with self._session_factory() as session:
            recipients = await session.scalar(
                select(func.count()).where(Client.bot_blocked_at.is_(None))
            )
            blocked = await session.scalar(
                select(func.count()).where(Client.bot_blocked_at.is_not(None))
            )
        return Audience(recipients or 0, blocked or 0)

    async def start(
        self,
        admin_id: int,
        preview: PreviewRef,
        content: BroadcastContent,
        reporter: BroadcastReporter,
    ) -> BroadcastStart:
        """«✅ Отправить»: записать запуск в журнал и начать рассылку в фоне.

        `content` — ровно то, что администратор видел в предпросмотре.
        """
        async with self._lock:
            async with self._session_factory() as session:
                if await _preview_handled(session, preview):
                    return BroadcastStart(StartOutcome.ALREADY_HANDLED)
                if self.running:
                    return BroadcastStart(StartOutcome.BUSY)
                recipients = await _recipients(session)
                if not recipients:
                    return BroadcastStart(StartOutcome.NO_RECIPIENTS)
                await self._record(
                    session,
                    admin_id,
                    BroadcastActionKind.BROADCAST,
                    preview_chat_id=preview.chat_id,
                    preview_message_id=preview.message_id,
                    recipients=len(recipients),
                    **_content_details(content),
                )
                await session.commit()
            self._task = asyncio.create_task(
                self._run(recipients, content, reporter), name="broadcast"
            )
        logger.info("Администратор %s запустил рассылку на %s клиентов", admin_id, len(recipients))
        return BroadcastStart(StartOutcome.STARTED, len(recipients))

    async def cancel(self, admin_id: int, preview: PreviewRef) -> bool:
        """«✖️ Отмена» под предпросмотром. `False` — он уже отправлен или отменён."""
        async with self._lock, self._session_factory() as session:
            if await _preview_handled(session, preview):
                return False
            await self._record(
                session,
                admin_id,
                BroadcastActionKind.CANCELLED,
                preview_chat_id=preview.chat_id,
                preview_message_id=preview.message_id,
            )
            await session.commit()
        return True

    async def stop(self, admin_id: int) -> bool:
        """«⏹ Остановить» идущую рассылку. `False` — рассылка не идёт."""
        if not self.running:
            return False
        async with self._session_factory() as session:
            await self._record(session, admin_id, BroadcastActionKind.STOPPED)
            await session.commit()
        logger.info("Администратор %s остановил рассылку", admin_id)
        await self._cancel_task()
        return True

    async def shutdown(self) -> None:
        """Остановка бота: прервать рассылку (отчёт «остановлена» уйдёт в тему)."""
        if self.running:
            logger.warning("Бот останавливается во время рассылки — прерываем её")
        await self._cancel_task()

    async def wait(self) -> None:
        """Дождаться конца идущей рассылки (для тестов)."""
        if self._task is not None:
            await self._task

    # --- Сама рассылка ---

    async def _cancel_task(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        # задача сама отправляет отчёт и завершается отменой — это ожидаемо
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(
        self, recipients: list[int], content: BroadcastContent, reporter: BroadcastReporter
    ) -> None:
        started_at = utc_now(self._clock)
        progress = BroadcastProgress(total=len(recipients))

        async def finish(end: BroadcastEnd, error: str | None = None) -> None:
            report = BroadcastReport(end, progress, started_at, utc_now(self._clock), error)
            logger.info("Рассылка: %s, %s", end.value, progress)
            await reporter.finished(report)

        try:
            await reporter.started(progress.total)
            failures_in_row = 0
            for telegram_id in recipients:
                outcome = await self._deliver(telegram_id, content)
                progress = _counted(progress, outcome)
                failures_in_row = failures_in_row + 1 if outcome is DeliveryOutcome.FAILED else 0
                if failures_in_row >= MAX_CONSECUTIVE_FAILURES:
                    logger.error("Рассылка остановлена: %s неудач подряд", failures_in_row)
                    await finish(BroadcastEnd.ABORTED)
                    return
                if progress.processed % PROGRESS_EVERY == 0 and progress.processed < progress.total:
                    await reporter.progress(progress)
                await self._sleep(BROADCAST_SEND_INTERVAL.total_seconds())
        except asyncio.CancelledError:
            await finish(BroadcastEnd.STOPPED)
            raise
        # Фоновая задача: ошибку некому поймать выше. Не прячем — трассировка в лог,
        # администраторам — сколько успело уйти (иначе они не узнают, что рассылка встала)
        except Exception as error:
            logger.exception("Рассылка упала")
            await finish(BroadcastEnd.CRASHED, f"{type(error).__name__}: {error}")
            return
        await finish(BroadcastEnd.COMPLETED)

    async def _deliver(self, telegram_id: int, content: BroadcastContent) -> DeliveryOutcome:
        for _ in range(MAX_THROTTLE_RETRIES + 1):
            try:
                return await self._sender.send(telegram_id, content)
            except BroadcastThrottled as throttled:
                logger.info("Рассылка: Telegram просит подождать %s с", throttled.retry_after)
                await self._sleep(throttled.retry_after)
        logger.warning("Рассылка: клиенту %s не отправлено — Telegram просит ждать", telegram_id)
        return DeliveryOutcome.FAILED

    async def _record(
        self,
        session: AsyncSession,
        admin_id: int,
        action: BroadcastActionKind,
        **details: object,
    ) -> None:
        await self._events.record(
            session,
            EventKind.ADMIN_ACTION,
            admin_id=admin_id,
            details={"action": action.value, **details},
        )


def _content_details(content: BroadcastContent) -> dict[str, object]:
    """Что отправлено — для записи о запуске в журнале."""
    match content:
        case BroadcastText(html=html):
            return {"text": html}
        case BroadcastCopy(chat_id=chat_id, message_id=message_id):
            return {"source_chat_id": chat_id, "source_message_id": message_id}


def _counted(progress: BroadcastProgress, outcome: DeliveryOutcome) -> BroadcastProgress:
    match outcome:
        case DeliveryOutcome.DELIVERED:
            return replace(progress, delivered=progress.delivered + 1)
        case DeliveryOutcome.BLOCKED:
            return replace(progress, blocked=progress.blocked + 1)
        case DeliveryOutcome.FAILED:
            return replace(progress, failed=progress.failed + 1)


async def _recipients(session: AsyncSession) -> list[int]:
    """Telegram ID клиентов бота, которые не заблокировали бота, — в порядке появления."""
    return list(
        await session.scalars(
            select(Client.telegram_id).where(Client.bot_blocked_at.is_(None)).order_by(Client.id)
        )
    )


async def _preview_handled(session: AsyncSession, preview: PreviewRef) -> bool:
    """Этот предпросмотр уже отправлен или отменён (запись в журнале)."""
    found = await session.scalar(
        select(Event.id)
        .where(
            Event.kind == EventKind.ADMIN_ACTION.value,
            Event.details["action"].as_string().in_(HANDLED_PREVIEW_ACTIONS),
            Event.details["preview_chat_id"].as_integer() == preview.chat_id,
            Event.details["preview_message_id"].as_integer() == preview.message_id,
        )
        .limit(1)
    )
    return found is not None
