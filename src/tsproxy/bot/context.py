"""Зависимости обработчиков Telegram — собираются один раз в `tsproxy.app`.

aiogram передаёт объект в обработчик по имени аргумента: `async def handler(..., ctx: BotContext)`.
"""

from dataclasses import dataclass

from aiogram.types import User

from tsproxy.bot.admin import AdminAccess
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import Screens
from tsproxy.content.loader import Texts
from tsproxy.integrations.remnawave_types import RemnawaveClient
from tsproxy.services.alerts import Alerter
from tsproxy.services.clients import ClientProfile, ClientService
from tsproxy.services.events import EventLog
from tsproxy.services.profile import ProfileService

# Имя, под которым контекст лежит в данных диспетчера
CONTEXT_KEY = "ctx"


@dataclass(frozen=True)
class BotContext:
    texts: Texts
    admin_texts: Texts
    screens: Screens
    clients: ClientService
    remnawave: RemnawaveClient
    delivery: ClientDelivery
    alerter: Alerter
    events: EventLog
    access: AdminAccess
    profiles: ProfileService


def profile_of(user: User) -> ClientProfile:
    """Данные клиента из Telegram. Имя — как его видит Telegram (имя и фамилия)."""
    return ClientProfile(telegram_id=user.id, name=user.full_name, username=user.username)
