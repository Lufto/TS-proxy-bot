"""Сборка и запуск всего бота в одном процессе (composition root).

Здесь — и только здесь — создаются настоящие зависимости: база, клиент Remnawave, бот
Telegram, журнал событий, тревоги, тексты. Остальной код получает их снаружи (правило 3
`docs/python-rules.md`), поэтому в тестах вместо них подставляются подделки.

Один процесс, три части (задача 09):
- бот aiogram: на Mac (`RUN_MODE=dev`) — polling, перед ним бот снимает webhook;
  на сервере (`RUN_MODE=prod`) — webhook с секретом, бот ставит его при старте;
- веб-сервер FastAPI (uvicorn): вебхук Telegram в боевом режиме, вебхуки Remnawave
  (если задан `REMNAWAVE_WEBHOOK_SECRET`), позже — платёжка и `/health`;
- фоновые задачи: сводки повторов тревог раз в минуту; сверка с Remnawave раз в 5 минут
  и сразу при запуске (задача 09).

Ctrl+C или `systemctl stop` (SIGINT/SIGTERM) — мягкая остановка: бот дорабатывает
текущие апдейты, затем закрываются HTTP-клиенты и база.
"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable, Generator, Sequence
from dataclasses import dataclass
from datetime import timedelta

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from tsproxy.bot.admin import AdminAccess
from tsproxy.bot.alert_sender import TelegramAlertSender
from tsproxy.bot.connect import REMNAWAVE_UNAVAILABLE_ALERT
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.dispatcher import bot_commands, create_dispatcher
from tsproxy.bot.screens import Screens
from tsproxy.bot.support_guard import SupportGuard, support_rate_limiter
from tsproxy.bot.throttling import RateLimiter
from tsproxy.config import PaymentsMode, RunMode, Settings
from tsproxy.content.loader import Content
from tsproxy.db.engine import create_engine, create_session_factory
from tsproxy.integrations.fake_payments import FakePaymentLedger, FakePaymentProvider
from tsproxy.integrations.payments_types import PaymentProvider
from tsproxy.integrations.remnawave import RemnawaveApiClient, create_http_client
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.logging_setup import MaskingFilter
from tsproxy.services.account_checks import AccountCheckHandler, AccountChecks
from tsproxy.services.alerts import Alerter
from tsproxy.services.clients import ClientService
from tsproxy.services.clock import Clock, system_clock
from tsproxy.services.events import EventLog
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.nodes import NodeMonitor
from tsproxy.services.orders import OrderService
from tsproxy.services.profile import ProfileService
from tsproxy.services.reconciliation import Reconciliation
from tsproxy.services.remnawave_signals import RemnawaveSignals
from tsproxy.services.support import SupportService
from tsproxy.services.trial import TrialService
from tsproxy.web.app import create_web_app
from tsproxy.web.remnawave import RemnawaveWebhook
from tsproxy.web.telegram import TelegramWebhook, webhook_url

logger = logging.getLogger(__name__)

# Сводки повторов тревог (`Alerter.flush_repeats`) — раз в минуту (задача 04)
ALERT_FLUSH_INTERVAL = timedelta(minutes=1)
# Сверка с Remnawave — страховка от потерянных вебхуков (задача 09: «каждые 5–10 минут»)
RECONCILE_INTERVAL = timedelta(minutes=5)
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


# --- Фоновые задачи ---


@dataclass(frozen=True)
class PeriodicJob:
    """Фоновая задача «раз в `interval`»."""

    name: str
    interval: timedelta
    run: Callable[[], Awaitable[None]]
    run_at_start: bool = False  # выполнить сразу при запуске, не дожидаясь интервала


class PeriodicJobs:
    """Простой планировщик: каждая задача — свой цикл «подождать, выполнить».

    Точка подключения для следующих задач (сверка с Remnawave, бэкапы, напоминания).
    """

    def __init__(self, jobs: Sequence[PeriodicJob]) -> None:
        self._jobs = tuple(jobs)
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._loop(job), name=f"job:{job.name}") for job in self._jobs
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def _loop(self, job: PeriodicJob) -> None:
        if job.run_at_start:
            await self._run_once(job)
        while True:
            await asyncio.sleep(job.interval.total_seconds())
            await self._run_once(job)

    async def _run_once(self, job: PeriodicJob) -> None:
        try:
            await job.run()
        # Одна неудача не должна останавливать задачу навсегда: пишем в лог с трассировкой
        # и пробуем в следующий раз. Сторож зависших задач — задача 22.
        except Exception:
            logger.exception("Фоновая задача %s завершилась ошибкой", job.name)


# --- Сборка ---


@dataclass(frozen=True)
class Application:
    """Всё собранное приложение: что запускать и что закрывать при остановке."""

    settings: Settings
    content: Content
    engine: AsyncEngine
    remnawave: RemnawaveApiClient
    bot: Bot
    dispatcher: Dispatcher
    web: FastAPI
    alerter: Alerter
    jobs: PeriodicJobs


def build_application(
    settings: Settings,
    content: Content,
    *,
    clock: Clock = system_clock,
    telegram_session: BaseSession | None = None,
    payments: PaymentProvider | None = None,
) -> Application:
    """Создать все зависимости. Сеть здесь не трогается — только при `run_application`.

    `telegram_session` — подделка Telegram в тестах; по умолчанию настоящая (aiohttp).
    `payments` — платёжка в тестах; по умолчанию — по `PAYMENTS_MODE`.
    """
    mask = MaskingFilter(settings.secret_values()).mask
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    remnawave = RemnawaveApiClient(
        create_http_client(settings.remnawave_url, settings.remnawave_token.get_secret_value()),
        settings.run_mode,
        prod_squad_name=settings.remnawave_squad,
    )
    bot = Bot(
        settings.bot_token.get_secret_value(),
        session=telegram_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    alerter = Alerter(
        TelegramAlertSender(bot),
        content.admin_texts,
        settings.admin_chat_id,
        settings.alerts_thread_id,
        clock,
        mask,
    )
    clients = ClientService(session_factory, clock)
    events = EventLog(clock, mask)
    orders = OrderService(
        session_factory,
        payments if payments is not None else _payment_provider(settings, clock),
        events,
        content.texts,
        content.tariffs,
        settings.admin_ids,
        clock,
        KeyedLocks(),
    )
    ctx = BotContext(
        texts=content.texts,
        admin_texts=content.admin_texts,
        screens=Screens(content.texts),
        clients=clients,
        remnawave=remnawave,
        delivery=ClientDelivery(bot, clients),
        alerter=alerter,
        events=events,
        access=AdminAccess(settings.admin_ids, settings.admin_chat_id),
        trials=TrialService(session_factory, remnawave, events, clock),
        profiles=ProfileService(remnawave, clock),
        support=SupportService(session_factory, clock, mask),
        support_guard=SupportGuard(support_rate_limiter()),
        orders=orders,
    )
    dispatcher = create_dispatcher(ctx, RateLimiter())
    # Обработчики проверок прокси-аккаунтов: их вызывают и вебхуки Remnawave, и сверка.
    # Задача 10 — напоминания и продления пробного, задача 11 — льготный период:
    # добавлять в этот список (AccountChecks копирует его при создании).
    account_handlers: list[AccountCheckHandler] = []
    account_checks = AccountChecks(remnawave, settings.run_mode, clock, account_handlers)
    nodes = NodeMonitor(remnawave, session_factory, alerter, content.admin_texts, clock)
    reconciliation = Reconciliation(account_checks, nodes, alerter, content.admin_texts)
    remnawave_webhook = _remnawave_webhook(
        settings, RemnawaveSignals(account_checks, nodes), alerter, content, clock
    )
    return Application(
        settings=settings,
        content=content,
        engine=engine,
        remnawave=remnawave,
        bot=bot,
        dispatcher=dispatcher,
        web=create_web_app(_telegram_webhook(settings, bot, dispatcher), remnawave_webhook),
        alerter=alerter,
        jobs=PeriodicJobs(
            [
                PeriodicJob("alerts_flush", ALERT_FLUSH_INTERVAL, alerter.flush_repeats),
                PeriodicJob(
                    "remnawave_reconcile",
                    RECONCILE_INTERVAL,
                    reconciliation.run,
                    run_at_start=True,  # бот лежал — сразу догнать пропущенное (задача 09)
                ),
            ]
        ),
    )


def _payment_provider(settings: Settings, clock: Clock) -> PaymentProvider:
    """Платёжка по `PAYMENTS_MODE`. Поддельная в боевом режиме не стартует (проверяют настройки)."""
    if settings.payments_mode is PaymentsMode.FAKE:
        return FakePaymentProvider(clock, FakePaymentLedger(settings.fake_payments_file))
    raise ValueError("PAYMENTS_MODE=real: настоящая платёжка ещё не подключена (задача 15)")


def _telegram_webhook(
    settings: Settings, bot: Bot, dispatcher: Dispatcher
) -> TelegramWebhook | None:
    """Приёмник вебхука Telegram — только в боевом режиме."""
    if settings.run_mode is not RunMode.PROD:
        return None
    if settings.telegram_webhook_secret is None:
        raise ValueError("TELEGRAM_WEBHOOK_SECRET обязателен в режиме prod")
    return TelegramWebhook(bot, dispatcher, settings.telegram_webhook_secret.get_secret_value())


def _remnawave_webhook(
    settings: Settings,
    signals: RemnawaveSignals,
    alerter: Alerter,
    content: Content,
    clock: Clock,
) -> RemnawaveWebhook | None:
    """Приёмник вебхуков Remnawave — только если задан секрет подписи."""
    if settings.remnawave_webhook_secret is None:
        logger.warning("REMNAWAVE_WEBHOOK_SECRET не задан: вебхуки Remnawave выключены")
        return None
    return RemnawaveWebhook(
        signals,
        settings.remnawave_webhook_secret.get_secret_value(),
        alerter,
        content.admin_texts,
        clock,
    )


# --- Запуск ---


class _WebServer(uvicorn.Server):
    """uvicorn без своих обработчиков сигналов: остановкой управляет `run_application`."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


async def _check_panel(app: Application) -> None:
    """Версия панели Remnawave. Не та основная версия или неверный токен — бот не стартует.

    Панель недоступна — бот всё равно стартует (меню и инструкции работают), но с тревогой.
    """
    try:
        version = await app.remnawave.check_panel_version()
    except RemnawaveUnavailable as error:
        logger.warning("Remnawave недоступна при старте: %s", error)
        await app.alerter.raise_alert(
            REMNAWAVE_UNAVAILABLE_ALERT, app.content.admin_texts.get("alerts.remnawave_at_start")
        )
        return
    logger.info("Remnawave %s", version)


async def connect_telegram(app: Application) -> None:
    """Команды бота и способ получения апдейтов: снять webhook (dev) или поставить (prod)."""
    settings = app.settings
    await app.bot.set_my_commands(bot_commands(app.content.texts))
    if settings.run_mode is RunMode.DEV:
        # Без этого polling не получит ни одного апдейта, пока стоит webhook
        await app.bot.delete_webhook(drop_pending_updates=False)
        logger.info("Режим dev: webhook снят, получаем апдейты через polling")
        return
    if settings.public_base_url is None or settings.telegram_webhook_secret is None:
        raise ValueError("PUBLIC_BASE_URL и TELEGRAM_WEBHOOK_SECRET обязательны в режиме prod")
    await app.bot.set_webhook(
        webhook_url(settings.public_base_url),
        secret_token=settings.telegram_webhook_secret.get_secret_value(),
        allowed_updates=app.dispatcher.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    logger.info("Режим prod: webhook установлен на %s", webhook_url(settings.public_base_url))


async def close_application(app: Application) -> None:
    """Закрыть HTTP-клиенты и базу. Каждое закрытие — даже если предыдущее упало."""
    try:
        await app.bot.session.close()
    finally:
        try:
            await app.remnawave.aclose()
        finally:
            await app.engine.dispose()


async def run_application(app: Application) -> None:
    """Запустить бота, веб-сервер и фоновые задачи; работать до SIGINT/SIGTERM."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in STOP_SIGNALS:
        loop.add_signal_handler(stop_signal, stop.set)
    settings = app.settings
    server = _WebServer(
        uvicorn.Config(
            app.web,
            host=settings.web_host,
            port=settings.web_port,
            log_config=None,  # пишем в наш логгер с маскировкой секретов
            access_log=False,
            server_header=False,
            lifespan="off",
        )
    )
    tasks: list[asyncio.Task[None]] = []
    polling: asyncio.Task[None] | None = None
    try:
        await _check_panel(app)
        await connect_telegram(app)
        tasks.append(asyncio.create_task(server.serve(), name="web"))
        if settings.run_mode is RunMode.DEV:
            polling = asyncio.create_task(
                app.dispatcher.start_polling(
                    app.bot,
                    handle_signals=False,
                    close_bot_session=False,
                    allowed_updates=app.dispatcher.resolve_used_update_types(),
                ),
                name="polling",
            )
            tasks.append(polling)
        app.jobs.start()
        logger.info("Бот запущен (%s)", settings.run_mode.value)
        waiter = asyncio.create_task(stop.wait(), name="stop")
        await asyncio.wait([*tasks, waiter], return_when=asyncio.FIRST_COMPLETED)
        if not stop.is_set():
            logger.error("Веб-сервер или polling остановились сами — останавливаем бота")
        waiter.cancel()
    finally:
        logger.info("Останавливаемся")
        for stop_signal in STOP_SIGNALS:
            loop.remove_signal_handler(stop_signal)
        await app.jobs.stop()
        await _stop_polling(app.dispatcher, polling)
        server.should_exit = True
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await close_application(app)
        logger.info("Бот остановлен")
    # Если какая-то часть упала — её ошибка всплывает здесь, после остановки остальных
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            raise result


async def _stop_polling(dispatcher: Dispatcher, polling: asyncio.Task[None] | None) -> None:
    if polling is None or polling.done():
        return
    try:
        await dispatcher.stop_polling()
    except RuntimeError:
        # polling ещё не успел начаться — просто отменяем задачу
        logger.info("Polling ещё не начался — отменяем")
        polling.cancel()
