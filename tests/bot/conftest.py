from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, ALERTS_THREAD_ID, BOT_TOKEN, NOW
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.bot.admin import AdminAccess
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.dispatcher import create_dispatcher
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.screens import Screens
from tsproxy.bot.support_guard import SupportGuard, support_rate_limiter
from tsproxy.bot.throttling import RateLimiter
from tsproxy.config import RunMode
from tsproxy.content.loader import Content, load_content
from tsproxy.db.engine import create_engine, create_session_factory
from tsproxy.db.models import Base
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.logging_setup import MaskingFilter
from tsproxy.services.admin import AdminService
from tsproxy.services.alerts import Alerter
from tsproxy.services.client_card import ClientCardService
from tsproxy.services.clients import ClientService
from tsproxy.services.events import EventLog
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.orders import OrderService
from tsproxy.services.profile import ProfileService
from tsproxy.services.support import SupportService
from tsproxy.services.trial import TrialService
from tsproxy.services.trial_switch import TrialSwitch

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"


class ManualMonotonic:
    """Монотонные часы ограничителя частоты, которые двигает тест."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture(scope="session")
def content() -> Content:
    return load_content(REPO_CONTENT)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def telegram() -> FakeTelegramSession:
    return FakeTelegramSession()


@pytest.fixture
def bot(telegram: FakeTelegramSession) -> Bot:
    return Bot(BOT_TOKEN, session=telegram, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def alert_sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def clients(session_factory: async_sessionmaker[AsyncSession], clock: FakeClock) -> ClientService:
    return ClientService(session_factory, clock)


@pytest.fixture
def payments(clock: FakeClock) -> FakePaymentProvider:
    return FakePaymentProvider(clock)


@pytest.fixture
def ctx(
    bot: Bot,
    content: Content,
    session_factory: async_sessionmaker[AsyncSession],
    clients: ClientService,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    clock: FakeClock,
    monotonic: ManualMonotonic,
    payments: FakePaymentProvider,
) -> BotContext:
    mask = MaskingFilter([BOT_TOKEN]).mask
    events = EventLog(clock, mask)
    alerter = Alerter(
        alert_sender, content.admin_texts, ADMIN_CHAT_ID, ALERTS_THREAD_ID, clock, mask
    )
    trial_switch = TrialSwitch(session_factory, events, alerter, content.admin_texts, clock)
    return BotContext(
        texts=content.texts,
        admin_texts=content.admin_texts,
        screens=Screens(content.texts),
        clients=clients,
        remnawave=remnawave,
        delivery=ClientDelivery(bot, clients),
        alerter=alerter,
        events=events,
        access=AdminAccess(frozenset({ADMIN_ID}), ADMIN_CHAT_ID),
        trials=TrialService(session_factory, remnawave, events, clock, trial_switch),
        profiles=ProfileService(remnawave, clock),
        support=SupportService(session_factory, clock, mask, events),
        support_guard=SupportGuard(support_rate_limiter(monotonic)),
        orders=OrderService(
            session_factory,
            payments,
            events,
            content.texts,
            content.tariffs,
            frozenset({ADMIN_ID}),
            clock,
            KeyedLocks(),
        ),
        client_cards=ClientCardService(session_factory, ProfileService(remnawave, clock)),
        admin=AdminService(
            session_factory,
            remnawave,
            ProfileService(remnawave, clock),
            events,
            clock,
            KeyedLocks(),
        ),
        trial_switch=trial_switch,
    )


@pytest.fixture
def monotonic() -> ManualMonotonic:
    return ManualMonotonic()


@pytest.fixture
def limiter(monotonic: ManualMonotonic) -> RateLimiter:
    return RateLimiter(clock=monotonic)


@pytest.fixture
def dispatcher(ctx: BotContext, limiter: RateLimiter) -> Dispatcher:
    return create_dispatcher(ctx, limiter)
