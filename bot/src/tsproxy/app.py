"""Сборка и запуск всего бота в одном процессе (composition root).

Здесь — и только здесь — создаются настоящие зависимости: база, клиент Remnawave, бот
Telegram, журнал событий, тревоги, тексты. Остальной код получает их снаружи (правило 3
`docs/python-rules.md`), поэтому в тестах вместо них подставляются подделки.

Один процесс, три части (задача 09):
- бот aiogram: на Mac (`RUN_MODE=dev`) — polling, перед ним бот снимает webhook;
  на сервере (`RUN_MODE=prod`) — webhook с секретом, бот ставит его при старте;
- веб-сервер FastAPI (uvicorn): вебхук Telegram в боевом режиме, вебхуки Remnawave
  (если задан `REMNAWAVE_WEBHOOK_SECRET`), уведомления платёжки (если оплата включена,
  задача 14), `/health` (задача 22);
- фоновые задачи: сводки повторов тревог раз в минуту; сверка с Remnawave раз в 5 минут
  и сразу при запуске (задача 09); повторы выдачи доступа после оплаты раз в минуту (задача 13);
  проверка оплат раз в 2 и раз в 30 минут и сверка с платёжкой в 09:00 МСК (задача 14);
  доделывание возвратов после сбоев раз в минуту (задача 21);
  сторож фоновых задач раз в минуту, присмотр за бэкапами панели раз в час, бэкап базы бота
  в 04:30 МСК (только `RUN_MODE=prod`), ежедневный отчёт в 10:00 МСК (задача 22);
  чистка старых данных в 05:00 МСК (`db/retention.py`).

Каждый запуск фоновой задачи отмечается в `JobHealth`: по отметкам сторож поднимает тревогу
о зависшей задаче, а `/health` отвечает «проблема».

`PAYMENTS_MODE=off` (задача 29) — тестовый запуск без платёжки: тарифы видны, купить нельзя;
при запуске — предупреждение в лог и тревога, чтобы не забыть включить оплату.

Ctrl+C или `systemctl stop` (SIGINT/SIGTERM) — мягкая остановка: бот дорабатывает
текущие апдейты, затем закрываются HTTP-клиенты и база.
"""

import asyncio
import contextlib
import functools
import logging
import signal
from collections.abc import Awaitable, Callable, Generator, Sequence
from dataclasses import dataclass
from datetime import time, timedelta

import httpx
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.bot.admin import AdminAccess
from tsproxy.bot.admin_announce import create_admin_announce_router
from tsproxy.bot.admin_broadcast import TelegramBroadcastSender, create_admin_broadcast_router
from tsproxy.bot.admin_compensation import (
    CompensationButtons,
    CompensationScreens,
    TelegramApologyMessenger,
    TelegramCompensationReporter,
    create_admin_compensation_router,
)
from tsproxy.bot.admin_proof import create_admin_proof_router
from tsproxy.bot.admin_site_banner import banner_alert_button
from tsproxy.bot.admin_stats import create_admin_stats_router
from tsproxy.bot.alert_sender import TelegramAlertSender
from tsproxy.bot.connect import REMNAWAVE_UNAVAILABLE_ALERT, deliver_pending_connects
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.dispatcher import bot_commands, create_dispatcher
from tsproxy.bot.documents import TelegramDocumentSender
from tsproxy.bot.grace import TelegramGraceMessenger
from tsproxy.bot.notices import NoticeScreens, TelegramNotifier
from tsproxy.bot.payments import PaymentScreens, TelegramPaymentMessages
from tsproxy.bot.refunds import RefundScreens, TelegramRefundMessages
from tsproxy.bot.screens import Screens
from tsproxy.bot.support_guard import SupportGuard, support_rate_limiter
from tsproxy.bot.support_reminders import SupportReminders
from tsproxy.bot.throttling import RateLimiter
from tsproxy.bot.trial import deliver_pending_trials
from tsproxy.config import PaymentsMode, RunMode, Settings
from tsproxy.content.loader import Content, Texts
from tsproxy.db.engine import create_engine, create_session_factory
from tsproxy.domain.monitoring import DAILY_JOB_STALE_AFTER, next_daily_run, periodic_stale_after
from tsproxy.integrations.fake_payments import FakePaymentLedger, FakePaymentProvider
from tsproxy.integrations.payments_types import PaymentProvider
from tsproxy.integrations.platega import PlategaPaymentProvider, create_platega_http_client
from tsproxy.integrations.refunds import refund_router
from tsproxy.integrations.remnawave import RemnawaveApiClient, create_http_client
from tsproxy.integrations.remnawave_types import (
    RemnawaveAuthError,
    RemnawaveUnavailable,
    UnexpectedPanelVersion,
)
from tsproxy.integrations.telegram_stars import STARS_PROVIDER, TelegramStarsRefunds
from tsproxy.jobs.backup import DatabaseBackup
from tsproxy.jobs.cleanup import DataCleanup
from tsproxy.jobs.daily_report import BotBackupJob, DailyReport, ReportDestination
from tsproxy.logging_setup import MaskingFilter
from tsproxy.services.account_checks import AccountCheckHandler, AccountChecks
from tsproxy.services.admin import AdminService
from tsproxy.services.admin_topics import AdminTopic
from tsproxy.services.alerts import Alerter
from tsproxy.services.announcement import AnnouncementService
from tsproxy.services.bot_errors import alert_bot_error
from tsproxy.services.broadcast import BroadcastService
from tsproxy.services.client_card import ClientCardService
from tsproxy.services.clients import ClientService
from tsproxy.services.clock import Clock, system_clock, utc_now
from tsproxy.services.compensation import CompensationService
from tsproxy.services.connect_waits import ConnectWaits
from tsproxy.services.crediting import CreditingService
from tsproxy.services.events import EventLog
from tsproxy.services.grace import GraceHandler
from tsproxy.services.health import HealthCheck
from tsproxy.services.job_health import JobHealth
from tsproxy.services.link_revoke import LinkRevokeService
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.nodes import NodeMonitor
from tsproxy.services.orders import OrderService
from tsproxy.services.panel_backups import PanelBackupMonitor
from tsproxy.services.payment_checks import PaymentChecks
from tsproxy.services.payment_reconciliation import PaymentReconciliation
from tsproxy.services.payment_status import PaymentStatusChanges
from tsproxy.services.profile import ProfileService
from tsproxy.services.reconciliation import Reconciliation
from tsproxy.services.refunds import RefundService
from tsproxy.services.reminders import ReminderService
from tsproxy.services.remnawave_signals import RemnawaveSignals
from tsproxy.services.service_proof import ServiceProofService
from tsproxy.services.site_banner import SiteBannerSwitch
from tsproxy.services.stars import StarsPayments
from tsproxy.services.stats import StatsService
from tsproxy.services.support import SupportService
from tsproxy.services.term_guard import TermLockedHandlers
from tsproxy.services.trial import TrialService
from tsproxy.services.trial_switch import TrialSwitch
from tsproxy.services.watchdog import JobWatchdog
from tsproxy.web.app import create_web_app
from tsproxy.web.health import HealthEndpoint
from tsproxy.web.payments import PaymentNotificationWebhook
from tsproxy.web.remnawave import RemnawaveWebhook
from tsproxy.web.status import StatusEndpoint
from tsproxy.web.telegram import TelegramWebhook, webhook_url

logger = logging.getLogger(__name__)

# Сводки повторов тревог (`Alerter.flush_repeats`) — раз в минуту (задача 04)
ALERT_FLUSH_INTERVAL = timedelta(minutes=1)
# Пробные, которые не выдались из-за панели, — доделываются сами (задача 07)
PENDING_TRIALS_INTERVAL = timedelta(minutes=1)
# «Подключиться» при сбое панели: раз в минуту прислать ссылку тем, кто ждёт
PENDING_CONNECTS_INTERVAL = timedelta(minutes=1)
# Ключ склейки тревоги «оплата выключена» (задача 29)
PAYMENTS_OFF_ALERT = "payments_off"
# Повторы выдачи доступа после оплаты — расписание каждого заказа в базе (задача 13)
PAYMENT_GRANTS_INTERVAL = timedelta(minutes=1)
# Проверка оплат в два темпа (задача 14): заказы младше 2 часов — раз в 2 минуты,
# от 2 до 72 часов — раз в 30 минут; сверка с платёжкой — раз в сутки в 09:00 МСК
FRESH_PAYMENT_CHECK_INTERVAL = timedelta(minutes=2)
STALE_PAYMENT_CHECK_INTERVAL = timedelta(minutes=30)
PAYMENTS_RECONCILE_AT = time(9, 0)  # по Москве, до ежедневного отчёта
# Возвраты после сбоя платёжки или панели доделываются раз в минуту (задача 21)
REFUND_RETRIES_INTERVAL = timedelta(minutes=1)
# Возвраты «на проверке у платёжки» — спросить её раз в 10 минут (задача 15)
REFUND_REVIEWS_INTERVAL = timedelta(minutes=10)
# Забытые обращения (нет ответа дольше часа) — проверка раз в 5 минут (задача 17)
SUPPORT_REMINDERS_INTERVAL = timedelta(minutes=5)
# Сверка с Remnawave — страховка от потерянных вебхуков (задача 09: «каждые 5–10 минут»)
RECONCILE_INTERVAL = timedelta(minutes=5)
# Сторож фоновых задач (задача 22)
WATCHDOG_INTERVAL = timedelta(minutes=1)
# Бэкап панели идёт раз в сутки — проверять его чаще раза в час незачем (задача 22)
PANEL_BACKUP_CHECK_INTERVAL = timedelta(hours=1)
# Бэкап базы бота — через полчаса после бэкапа панели в 04:00 МСК (задача 23)
BOT_BACKUP_AT = time(4, 30)
# Чистка старых данных — после бэкапа базы бота
DATA_CLEANUP_AT = time(5, 0)
# Ежедневный отчёт (задача 18 в vpn-bot-rework)
DAILY_REPORT_AT = time(10, 0)
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)
# Панель не пустила бота при запуске — ждём перед выходом: systemd перезапускает бота через 5 с,
# а счётчики склейки тревог в памяти, и без паузы тревога уходила бы каждые 5 секунд
FATAL_START_PAUSE = timedelta(minutes=10)
REMNAWAVE_AT_START_ALERT = "remnawave_at_start"
REMNAWAVE_START_REFUSED_ALERT = "remnawave_start_refused"


# --- Фоновые задачи ---


@dataclass(frozen=True)
class PeriodicJob:
    """Фоновая задача «раз в `interval`»."""

    name: str
    interval: timedelta
    run: Callable[[], Awaitable[None]]
    run_at_start: bool = False  # выполнить сразу при запуске, не дожидаясь интервала


@dataclass(frozen=True)
class DailyJob:
    """Фоновая задача «раз в сутки в `at` по Москве» (бэкап, отчёт)."""

    name: str
    at: time
    run: Callable[[], Awaitable[None]]


type Job = PeriodicJob | DailyJob


def stale_after(job: Job) -> timedelta:
    """Сколько задача может не отрабатывать успешно, пока сторож не поднимет тревогу."""
    if isinstance(job, DailyJob):
        return DAILY_JOB_STALE_AFTER
    return periodic_stale_after(job.interval)


class PeriodicJobs:
    """Простой планировщик: каждая задача — свой цикл «подождать, выполнить».

    Каждый запуск отмечается в `health` (начала, успешно закончила, упала) — по этим
    отметкам работают сторож фоновых задач и `/health` (задача 22). Упавший запуск
    передаётся в `on_error` (имя задачи и ошибка) — сборка поднимает «Ошибку в боте».
    """

    def __init__(
        self,
        jobs: Sequence[Job],
        health: JobHealth | None = None,
        clock: Clock = system_clock,
        on_error: Callable[[str, Exception], Awaitable[None]] | None = None,
    ) -> None:
        self._jobs = tuple(jobs)
        self._clock = clock
        self._on_error = on_error
        self.health = health if health is not None else JobHealth(clock)
        for job in self._jobs:
            self.health.watch(job.name, stale_after(job))
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

    async def _loop(self, job: Job) -> None:
        if isinstance(job, DailyJob):
            await self._daily_loop(job)
            return
        if job.run_at_start:
            await self._run_once(job)
        while True:
            await asyncio.sleep(job.interval.total_seconds())
            await self._run_once(job)

    async def _daily_loop(self, job: DailyJob) -> None:
        target = next_daily_run(utc_now(self._clock), job.at)
        while True:
            remaining = (target - utc_now(self._clock)).total_seconds()
            if remaining > 0:
                # sleep может проснуться чуть раньше срока — тогда доспать, а не запустить дважды
                await asyncio.sleep(remaining)
                continue
            await self._run_once(job)
            target = next_daily_run(max(target, utc_now(self._clock)), job.at)

    async def _run_once(self, job: Job) -> None:
        self.health.started(job.name)
        try:
            await job.run()
        # Одна неудача не должна останавливать задачу навсегда: пишем в лог с трассировкой
        # и пробуем в следующий раз. Отметку «упала» видит сторож фоновых задач.
        except Exception as error:
            self.health.failed(job.name)
            logger.exception("Фоновая задача %s завершилась ошибкой", job.name)
            if self._on_error is not None:
                await self._on_error(job.name, error)
            return
        self.health.succeeded(job.name)


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
    broadcasts: BroadcastService  # рассылка (задача 19): при остановке бота — прервать с отчётом
    # HTTP-клиент Platega (задача 15) — закрыть при остановке; `None` — другая платёжка
    payments_http: httpx.AsyncClient | None = None


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
        # Задача сайта 15: под «Remnawave не отвечает» — кнопка «Поставить плашку» на сайт
        buttons={REMNAWAVE_UNAVAILABLE_ALERT: banner_alert_button(content.admin_texts)},
    )
    clients = ClientService(session_factory, clock)
    events = EventLog(clock, mask)
    payments_http = (
        create_platega_http_client(settings.platega_base_url)
        if payments is None and settings.payments_mode is PaymentsMode.PLATEGA
        else None
    )
    provider = (
        payments if payments is not None else _payment_provider(settings, clock, payments_http)
    )
    profiles = ProfileService(remnawave, clock)
    screens = Screens(content.texts)
    delivery = ClientDelivery(bot, clients)
    # Одна блокировка на клиента для всего, что меняет срок подписки: зачёт оплаты,
    # ручное продление (задача 18), компенсация (задача 20). Иначе два изменения сразу
    # посчитают новый срок от одной и той же старой даты, и дни потеряются.
    term_locks = KeyedLocks()
    # Зачёт оплаты и выдача доступа (задача 13). Его вызывают уведомления платёжки,
    # «Проверить оплату» и фоновая проверка (задача 14)
    payments_topic = AdminTopic(
        TelegramAlertSender(bot), settings.admin_chat_id, settings.payments_thread_id
    )
    payment_messages = TelegramPaymentMessages(delivery, PaymentScreens(content.texts, screens))
    crediting = CreditingService(
        session_factory,
        remnawave,
        events,
        alerter,
        payments_topic,
        content.admin_texts,
        payment_messages,
        clock,
        term_locks,
    )
    # Задача 33: звёзды Telegram — второй способ оплаты. Возврат звёзд — через Telegram,
    # он работает и при выключенной оплате (вернуть уже оплаченное звёздами)
    stars_refunds = TelegramStarsRefunds(bot)
    # Возвраты (задача 21): деньги — тем же способом, которым платили (платёжка или звёзды),
    # срок — под той же блокировкой
    refunds = RefundService(
        session_factory,
        remnawave,
        refund_router(provider, **{STARS_PROVIDER: stars_refunds}),
        events,
        alerter,
        payments_topic,
        content.admin_texts,
        TelegramRefundMessages(delivery, RefundScreens(content.texts)),
        clock,
        term_locks,
    )
    # Задача 15: статус оплаты изменился после оплаты — спор через банк, наш возврат, проверка
    status_changes = PaymentStatusChanges(
        session_factory,
        remnawave,
        refunds,
        events,
        alerter,
        content.admin_texts,
        payment_messages,
        term_locks,
        clock,
    )
    # Уведомления платёжки, «Проверить оплату», фоновая проверка, «Купить» (задача 14)
    payment_checks = PaymentChecks(
        session_factory,
        provider,
        crediting,
        events,
        alerter,
        content.admin_texts,
        clock,
        test_payments=_expects_test_payments(settings),
        status_changes=status_changes,
    )
    # Замки заказов: «Купить» берёт сначала их, потом (внутри зачёта) — замок срока
    orders = OrderService(
        session_factory,
        provider,
        events,
        content.texts,
        content.tariffs,
        clock,
        KeyedLocks(),
        payment_checks,
    )
    payments_reconciliation = PaymentReconciliation(
        session_factory, provider, payment_checks, alerter, content.admin_texts, clock
    )
    # Задача 33: звёзды Telegram — второй способ оплаты
    stars = StarsPayments(
        session_factory,
        orders,
        crediting,
        stars_refunds,
        events,
        alerter,
        content.admin_texts,
        clock,
        content.stars_rub_rate,
    )
    # Выключатель пробных (задача 28): состояние — последнее событие в журнале
    trial_switch = TrialSwitch(session_factory, events, alerter, clock)
    # Плашка на сайте (задачи сайта 13–15): тоже последнее событие в журнале
    site_banner = SiteBannerSwitch(session_factory, events, alerter, content.admin_texts)
    ctx = BotContext(
        texts=content.texts,
        admin_texts=content.admin_texts,
        screens=screens,
        clients=clients,
        remnawave=remnawave,
        delivery=delivery,
        alerter=alerter,
        events=events,
        access=AdminAccess(settings.admin_ids, settings.admin_chat_id),
        trials=TrialService(session_factory, remnawave, events, clock, trial_switch),
        profiles=profiles,
        support=SupportService(session_factory, clock, mask, events),
        support_guard=SupportGuard(support_rate_limiter()),
        orders=orders,
        client_cards=ClientCardService(session_factory, profiles),
        admin=AdminService(session_factory, remnawave, profiles, events, clock, term_locks),
        link_revokes=LinkRevokeService(session_factory, remnawave, events, clock, KeyedLocks()),
        trial_switch=trial_switch,
        payment_checks=payment_checks,
        refunds=refunds,
        stars=stars,
        site_banner=site_banner,
        connect_waits=ConnectWaits(session_factory, events, clock),
        clock=clock,
    )
    # Статистика и рассылка (задача 19)
    broadcasts = BroadcastService(
        session_factory, events, clock, TelegramBroadcastSender(bot, delivery)
    )
    # Компенсация при сбое ноды (задача 20): предложение — когда нода восстановилась,
    # начисление — по кнопке, под той же блокировкой срока, что у оплаты
    compensation_screens = CompensationScreens(content.admin_texts, content.texts)
    compensation = CompensationService(
        session_factory,
        remnawave,
        events,
        alerter,
        content.admin_texts,
        TelegramCompensationReporter(
            bot, compensation_screens, settings.admin_chat_id, settings.alerts_thread_id
        ),
        TelegramApologyMessenger(delivery, content.texts),
        clock,
        term_locks,
    )
    stats = StatsService(session_factory, remnawave, clock, content.stars_rub_rate)  # /stats, отчёт
    dispatcher = create_dispatcher(
        ctx,
        RateLimiter(),
        extra_routers=[
            create_admin_stats_router(ctx.access, stats),
            create_admin_broadcast_router(ctx.access, broadcasts),
            create_admin_compensation_router(
                ctx.access,
                CompensationButtons(compensation, compensation_screens, content.admin_texts),
            ),
            # задача 22: доказательство оказания услуги по заказу
            create_admin_proof_router(
                ctx.access, ServiceProofService(session_factory, remnawave, clock)
            ),
            # Перенос подписки (`/transfer`, задача 21) отключён решением владельца 2026-11-11:
            # с ним спор через банк оставлял дни на новом аккаунте. Код и тесты остаются;
            # вернуть — подключить `create_admin_transfer_router` с `TransferService` здесь.
            # задача 35: объявление клиентам в приложении (настройки подписки панели)
            create_admin_announce_router(
                ctx.access, AnnouncementService(session_factory, remnawave, events)
            ),
        ],
    )
    # Обработчики проверок прокси-аккаунтов: их вызывают и вебхуки Remnawave, и сверка.
    # Задача 10 — напоминания и продления пробного, задача 11 — льготный период:
    # добавлять в этот список (AccountChecks копирует его при создании).
    # Оба меняют срок — под общей блокировкой срока и по свежему аккаунту (`term_guard`)
    term_locked = TermLockedHandlers(
        [
            GraceHandler(
                session_factory,
                remnawave,
                events,
                TelegramGraceMessenger(ctx.delivery, content.texts),
                clock,
            ),
            ReminderService(
                session_factory,
                remnawave,
                events,
                TelegramNotifier(NoticeScreens(content.texts), ctx.delivery),
                clock,
            ),
        ],
        session_factory,
        remnawave,
        term_locks,
    )
    account_handlers: list[AccountCheckHandler] = [term_locked]
    account_checks = AccountChecks(remnawave, settings.run_mode, clock, account_handlers)
    nodes = NodeMonitor(
        remnawave, session_factory, alerter, content.admin_texts, clock, on_closed=compensation
    )
    reconciliation = Reconciliation(account_checks, nodes, alerter, content.admin_texts)
    remnawave_webhook = _remnawave_webhook(
        settings, RemnawaveSignals(account_checks, nodes), alerter, clock
    )
    # Задача 22: отметки фоновых задач — для сторожа и /health
    job_health = JobHealth(clock)
    health = HealthCheck(session_factory, remnawave, job_health, clock)
    return Application(
        settings=settings,
        content=content,
        engine=engine,
        remnawave=remnawave,
        bot=bot,
        dispatcher=dispatcher,
        web=create_web_app(
            _telegram_webhook(settings, bot, dispatcher),
            remnawave_webhook,
            HealthEndpoint(health),
            # оплата выключена — уведомления не принимаем: перепроверить их не у кого
            None if provider is None else PaymentNotificationWebhook(provider, payment_checks),
            StatusEndpoint(site_banner),
        ),
        alerter=alerter,
        broadcasts=broadcasts,
        payments_http=payments_http,
        jobs=PeriodicJobs(
            [
                PeriodicJob("alerts_flush", ALERT_FLUSH_INTERVAL, alerter.flush_repeats),
                PeriodicJob(
                    "pending_trials",
                    PENDING_TRIALS_INTERVAL,
                    lambda: deliver_pending_trials(ctx),
                    run_at_start=True,
                ),
                PeriodicJob(
                    "pending_connects",
                    PENDING_CONNECTS_INTERVAL,
                    lambda: deliver_pending_connects(ctx),
                    run_at_start=True,
                ),
                # Работает и при PAYMENTS_MODE=off: доделывает выдачу по уже оплаченным
                # заказам (платёжка для этого не нужна, только Remnawave). Иначе после
                # переключения на off оплаченные, но не выданные заказы зависли бы.
                PeriodicJob(
                    "payment_grants",
                    PAYMENT_GRANTS_INTERVAL,
                    crediting.retry_grants,
                    run_at_start=True,  # бот лежал — сразу доделать выдачу оплаченного
                ),
                # Без платёжки (PAYMENTS_MODE=off) ничего не делают: спросить не у кого
                PeriodicJob(
                    "payment_checks_fresh",
                    FRESH_PAYMENT_CHECK_INTERVAL,
                    payment_checks.check_fresh,
                    run_at_start=True,  # бот лежал — сразу найти оплаты с потерянными уведомлениями
                ),
                PeriodicJob(
                    "payment_checks_stale",
                    STALE_PAYMENT_CHECK_INTERVAL,
                    payment_checks.check_stale,
                    run_at_start=True,
                ),
                DailyJob("payments_reconcile", PAYMENTS_RECONCILE_AT, payments_reconciliation.run),
                # Возвраты: платёжка не ответила — вернуть ещё раз с тем же ключом; деньги
                # вернулись, а панель не ответила — снять дни (задача 21)
                PeriodicJob(
                    "refund_retries",
                    REFUND_RETRIES_INTERVAL,
                    refunds.retry_unfinished,
                    run_at_start=True,
                ),
                # Возвраты «на проверке у платёжки»: потерянный итог и молчаливый отказ (задача 15)
                PeriodicJob(
                    "refund_reviews",
                    REFUND_REVIEWS_INTERVAL,
                    payment_checks.check_refunds,
                    run_at_start=True,
                ),
                PeriodicJob(
                    "remnawave_reconcile",
                    RECONCILE_INTERVAL,
                    reconciliation.run,
                    run_at_start=True,  # бот лежал — сразу догнать пропущенное (задача 09)
                ),
                PeriodicJob(
                    "support_reminders",
                    SUPPORT_REMINDERS_INTERVAL,
                    SupportReminders(bot, ctx).run,
                ),
                *_monitoring_jobs(
                    settings, content, bot, alerter, job_health, session_factory, stats, clock
                ),
            ],
            job_health,
            clock,
            on_error=functools.partial(_job_failed, alerter, content.admin_texts),
        ),
    )


async def _job_failed(alerter: Alerter, texts: Texts, name: str, error: Exception) -> None:
    """Фоновая задача упала — «Ошибка в боте» с её именем."""
    action = texts.get("bot_error_action.job", name=name)
    await alert_bot_error(alerter, texts, error, action)


def _monitoring_jobs(
    settings: Settings,
    content: Content,
    bot: Bot,
    alerter: Alerter,
    job_health: JobHealth,
    session_factory: async_sessionmaker[AsyncSession],
    stats: StatsService,
    clock: Clock,
) -> list[Job]:
    """Задача 22: сторож, присмотр за бэкапами панели, бэкап базы бота, ежедневный отчёт;
    чистка старых данных."""
    jobs: list[Job] = [
        PeriodicJob(
            "watchdog",
            WATCHDOG_INTERVAL,
            JobWatchdog(job_health, alerter, content.admin_texts).run,
        )
    ]
    panel_backups = _panel_backup_monitor(settings, alerter, content, clock)
    if panel_backups is not None:
        jobs.append(
            PeriodicJob(
                "panel_backup_check",
                PANEL_BACKUP_CHECK_INTERVAL,
                panel_backups.check,
                run_at_start=True,
            )
        )
    bot_backup = _bot_backup_job(settings, bot, alerter, clock)
    if bot_backup is not None:
        jobs.append(DailyJob("bot_backup", BOT_BACKUP_AT, bot_backup.run))
    report = DailyReport(
        session_factory,
        stats,
        alerter,
        job_health,
        panel_backups,
        settings.backup_dir,
        bot_backup,
        TelegramAlertSender(bot),
        ReportDestination(settings.admin_chat_id, settings.reports_thread_id),
        content.admin_texts,
        clock,
    )
    jobs.append(DailyJob("daily_report", DAILY_REPORT_AT, report.send))
    jobs.append(DailyJob("data_cleanup", DATA_CLEANUP_AT, DataCleanup(session_factory, clock).run))
    return jobs


def _panel_backup_monitor(
    settings: Settings, alerter: Alerter, content: Content, clock: Clock
) -> PanelBackupMonitor | None:
    """Присмотр за бэкапами панели — только на сервере и если заданы журнал и папка копий.

    На Mac (`RUN_MODE=dev`) панели и её копий нет: присмотр давал бы тревогу при каждом запуске.
    """
    if settings.run_mode is not RunMode.PROD:
        return None
    if settings.panel_backup_log is None or settings.panel_backup_dir is None:
        logger.warning(
            "PANEL_BACKUP_LOG или PANEL_BACKUP_DIR не заданы: бэкапы панели без присмотра"
        )
        return None
    return PanelBackupMonitor(
        settings.panel_backup_log, settings.panel_backup_dir, alerter, content.admin_texts, clock
    )


def _bot_backup_job(
    settings: Settings, bot: Bot, alerter: Alerter, clock: Clock
) -> BotBackupJob | None:
    """Бэкап базы бота (задача 23) — только на сервере.

    На Mac (`RUN_MODE=dev`) нет `age` и `/var/backups/tsproxy`, а копия тестовой базы
    в «💾 Бэкапы» рядом с настоящими только запутает. Создаётся при запуске, чтобы
    не-SQLite база остановила бота сразу, а не ночью.
    """
    if settings.run_mode is not RunMode.PROD:
        logger.info("Режим dev: бэкап базы бота по расписанию не запускается")
        return None
    backup = DatabaseBackup.from_settings(settings, TelegramDocumentSender(bot), alerter, clock)
    return BotBackupJob(backup)


def _payment_provider(
    settings: Settings, clock: Clock, platega_http: httpx.AsyncClient | None
) -> PaymentProvider | None:
    """Платёжка по `PAYMENTS_MODE`. Поддельная в боевом режиме не стартует (проверяют настройки).

    `None` — оплата выключена (задача 29): заказы и платежи не создаются.
    """
    match settings.payments_mode:
        case PaymentsMode.OFF:
            return None
        case PaymentsMode.FAKE:
            return FakePaymentProvider(clock, FakePaymentLedger(settings.fake_payments_file))
        case PaymentsMode.PLATEGA:
            return _platega(settings, clock, platega_http)


def _platega(
    settings: Settings, clock: Clock, http: httpx.AsyncClient | None
) -> PlategaPaymentProvider:
    """Platega (задача 15): ключи из `.env`, после оплаты — обратно в бота по ссылке t.me."""
    merchant_id, secret = settings.platega_merchant_id, settings.platega_secret
    if http is None or merchant_id is None or secret is None or settings.bot_username is None:
        raise ValueError("PAYMENTS_MODE=platega: нет ключей Platega или BOT_USERNAME")
    return PlategaPaymentProvider(
        http,
        merchant_id.get_secret_value(),
        secret.get_secret_value(),
        f"https://t.me/{settings.bot_username}",
        clock,
        test=settings.platega_test_account,
        # документация Platega: email обязателен, иначе форма спросит его у клиента;
        # передаём `<telegram_id>@telegram.org`, как она советует (владелец, 2026-10-31)
        send_customer_email=True,
    )


def _expects_test_payments(settings: Settings) -> bool:
    """Какие платежи засчитывать: в бою — только настоящие, в разработке — только тестовые
    (поддельная платёжка, тестовый магазин). Не совпало — не засчитываем (аудит R08)."""
    return settings.run_mode is not RunMode.PROD


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
        clock,
    )


# --- Запуск ---


class _WebServer(uvicorn.Server):
    """uvicorn без своих обработчиков сигналов: остановкой управляет `run_application`."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


async def check_panel(
    app: Application, stop: asyncio.Event, pause: timedelta = FATAL_START_PAUSE
) -> None:
    """Версия панели Remnawave. Не та основная версия или неверный токен — тревога и через
    `pause` (или по сигналу остановки) ошибка: бот не стартует.

    Панель недоступна — бот всё равно стартует (меню и инструкции работают), но с тревогой.
    """
    try:
        version = await app.remnawave.check_panel_version()
    except RemnawaveUnavailable as error:
        logger.warning("Remnawave недоступна при старте: %s", error)
        await app.alerter.alert(
            REMNAWAVE_AT_START_ALERT,
            source=check_panel,
            error=error,
            glue=REMNAWAVE_UNAVAILABLE_ALERT,
        )
        return
    except (RemnawaveAuthError, UnexpectedPanelVersion) as error:
        logger.error("Панель не пустила бота: %s — выходим через %s", error, pause)
        minutes = pause // timedelta(minutes=1)
        await app.alerter.alert(
            REMNAWAVE_START_REFUSED_ALERT, source=check_panel, error=error, minutes=minutes
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), pause.total_seconds())
        raise
    logger.info("Remnawave %s", version)


async def check_payments_mode(app: Application) -> None:
    """Оплата выключена — напомнить в лог и в «🚨 Тревоги», чтобы не забыть включить (задача 29)."""
    if app.settings.payments_mode is not PaymentsMode.OFF:
        return
    logger.warning("Оплата выключена (PAYMENTS_MODE=off): купить подписку нельзя, только пробный")
    await app.alerter.alert(PAYMENTS_OFF_ALERT, source=check_payments_mode)


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
            try:
                if app.payments_http is not None:
                    await app.payments_http.aclose()
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
        await check_panel(app, stop)
        await check_payments_mode(app)
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
        # после polling: новых нажатий «Отправить» уже не будет; отчёт уйдёт, пока бот открыт
        await app.broadcasts.shutdown()
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
