"""Зависимости обработчиков Telegram — собираются один раз в `tsproxy.app`.

aiogram передаёт объект в обработчик по имени аргумента: `async def handler(..., ctx: BotContext)`.
"""

from dataclasses import dataclass

from aiogram.types import User

from tsproxy.bot.admin import AdminAccess
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import Screens
from tsproxy.bot.support_guard import SupportGuard
from tsproxy.content.loader import Texts
from tsproxy.integrations.remnawave_types import RemnawaveClient
from tsproxy.services.admin import AdminService
from tsproxy.services.alerts import Alerter
from tsproxy.services.client_card import ClientCardService
from tsproxy.services.clients import ClientProfile, ClientService
from tsproxy.services.clock import Clock
from tsproxy.services.connect_waits import ConnectWaits
from tsproxy.services.events import EventLog
from tsproxy.services.link_revoke import LinkRevokeService
from tsproxy.services.orders import OrderService
from tsproxy.services.payment_checks import PaymentChecks
from tsproxy.services.profile import ProfileService
from tsproxy.services.refunds import RefundService
from tsproxy.services.site_banner import SiteBannerSwitch
from tsproxy.services.stars import StarsPayments
from tsproxy.services.support import SupportService
from tsproxy.services.trial import TrialService
from tsproxy.services.trial_switch import TrialSwitch

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
    trials: TrialService
    profiles: ProfileService
    support: SupportService
    support_guard: SupportGuard
    orders: OrderService  # тарифы и заказы (задача 12)
    client_cards: ClientCardService  # карточка клиента в теме обращения (задача 17)
    admin: AdminService  # поиск клиента и действия администратора (задача 18)
    link_revokes: LinkRevokeService  # клиент сам перевыпускает ссылку (задача 31)
    trial_switch: TrialSwitch  # выключатель пробных (задача 28), тот же, что у `trials`
    payment_checks: PaymentChecks  # «Проверить оплату» (задача 14), тот же, что у `orders`
    refunds: RefundService  # «↩️ Вернуть» и случайная вторая оплата (задача 21)
    stars: StarsPayments  # оплата звёздами Telegram (задача 33)
    site_banner: SiteBannerSwitch  # плашка о неполадках на сайте (задачи сайта 13–15)
    connect_waits: ConnectWaits  # «Подключиться» при сбое панели — бот пришлёт ссылку сам
    clock: Clock  # сутки простоя в живом меню (задача 26)


def profile_of(user: User) -> ClientProfile:
    """Данные клиента из Telegram. Имя — как его видит Telegram (имя и фамилия)."""
    return ClientProfile(telegram_id=user.id, name=user.full_name, username=user.username)
