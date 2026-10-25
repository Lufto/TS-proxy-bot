"""Сообщения бота в теме обращения (задача 17): карточка клиента, кнопки, быстрые ответы,
напоминание. Только сборка текста и кнопок из `content/admin_texts.yaml`, без вызовов Telegram.

Текст собирается из строк: каждая строка — свой текст со своими подстановками, поэтому
готовые куски не экранируются второй раз. Тексты заготовок вставляются как есть
(в них разметка владельца, подстановок нет).
"""

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.callbacks import QuickReplyCallback, TicketAction, TicketCallback
from tsproxy.bot.screens import Screen
from tsproxy.content.loader import QUICK_REPLIES_GROUP, Texts, quick_reply_ids
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.domain.profile import ProfileStatus
from tsproxy.services.client_card import ClientCard
from tsproxy.services.profile import Profile

LINE = "\n"
PARAGRAPH = "\n\n"

SUBSCRIPTION_TEXTS: dict[ProfileStatus, str] = {
    ProfileStatus.NO_SUBSCRIPTION: "support.card.subscription_none",
    ProfileStatus.TRIAL: "support.card.subscription_trial",
    ProfileStatus.TRIAL_TRAFFIC_USED_UP: "support.card.subscription_trial_traffic_used_up",
    ProfileStatus.ACTIVE: "support.card.subscription_active",
    ProfileStatus.GRACE: "support.card.subscription_grace",
    ProfileStatus.EXPIRED: "support.card.subscription_expired",
    ProfileStatus.SUSPENDED: "support.card.subscription_suspended",
}


class SupportTopicTexts:
    """Собирает сообщения бота в теме обращения."""

    def __init__(self, admin_texts: Texts) -> None:
        self._texts = admin_texts

    # --- Карточка клиента ---

    def card(self, ticket_id: int, card: ClientCard, *, reopened: bool) -> Screen:
        """Карточка в начале темы (или заново — при переоткрытии) и кнопки под ней."""
        title_key = "support.card.title_reopened" if reopened else "support.card.title"
        lines = [
            self._texts.get(title_key, ticket_id=ticket_id),
            self._texts.get(
                "support.card.client",
                name=card.name or "",
                username=card.username or self._texts.get("support.no_username"),
                telegram_id=card.telegram_id,
            ),
            *self._subscription(card),
            self._payments(card),
            self._trial(card),
        ]
        text = LINE.join(lines) + PARAGRAPH + self._texts.get("support.card.hint")
        return Screen(text, self.keyboard(ticket_id, client_actions=True))

    def _subscription(self, card: ClientCard) -> list[str]:
        """Строки подписки и устройств."""
        if card.panel_failed:
            return [self._texts.get("support.card.subscription_unknown")]
        if card.profile is None:
            return [self._texts.get("support.card.subscription_none")]
        return [self._status(card.profile), self._devices(card.profile)]

    def _status(self, profile: Profile) -> str:
        key = SUBSCRIPTION_TEXTS[profile.status]
        if profile.status is ProfileStatus.NO_SUBSCRIPTION:
            return self._texts.get(key)
        return self._texts.get(key, expire_at=format_moscow(profile.expire_at))

    def _devices(self, profile: Profile) -> str:
        slots = profile.devices
        if slots.limit is None:
            return self._texts.get("support.card.devices_no_limit", used=slots.used)
        return self._texts.get("support.card.devices", used=slots.used, limit=slots.limit)

    def _payments(self, card: ClientCard) -> str:
        payments = card.payments
        if payments.count == 0 or payments.last_paid_at is None:
            return self._texts.get("support.card.payments_none")
        return self._texts.get(
            "support.card.payments",
            count=payments.count,
            total=format_rubles(payments.total),
            last_paid_at=format_moscow(payments.last_paid_at),
        )

    def _trial(self, card: ClientCard) -> str:
        if card.trial_granted_at is None:
            return self._texts.get("support.card.trial_none")
        return self._texts.get(
            "support.card.trial", granted_at=format_moscow(card.trial_granted_at)
        )

    # --- Кнопки ---

    def _button(self, key: str, action: TicketAction, ticket_id: int) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self._texts.get(f"support.buttons.{key}"),
            callback_data=TicketCallback(action=action, ticket_id=ticket_id).pack(),
        )

    def keyboard(self, ticket_id: int, *, client_actions: bool) -> InlineKeyboardMarkup:
        """Кнопки темы. `client_actions` — ещё «➕ Продлить» и «📱 Устройства» (в карточке)."""
        rows = []
        if client_actions:
            rows.append(
                [
                    self._button("extend", TicketAction.EXTEND, ticket_id),
                    self._button("devices", TicketAction.DEVICES, ticket_id),
                ]
            )
        rows += [
            [
                self._button("take", TicketAction.TAKE, ticket_id),
                self._button("close", TicketAction.CLOSE, ticket_id),
            ],
            [self._button("quick_replies", TicketAction.QUICK_REPLIES, ticket_id)],
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    # --- «🙋 Беру», «✅ Закрыть», напоминание ---

    def taken(self, admin_name: str) -> str:
        return self._texts.get("support.taken", admin=admin_name)

    def closed(self, admin_name: str) -> str:
        return self._texts.get("support.closed", admin=admin_name)

    def reminder(self, ticket_id: int, since: datetime, assignee_admin_id: int | None) -> Screen:
        """Клиент ждёт ответа дольше часа. Взял кто-то — упоминаем его (придёт уведомление)."""
        if assignee_admin_id is None:
            text = self._texts.get("support.reminder", since=format_moscow(since))
        else:
            text = self._texts.get(
                "support.reminder_assignee",
                since=format_moscow(since),
                admin_id=assignee_admin_id,
            )
        return Screen(text, self.keyboard(ticket_id, client_actions=False))

    # --- Быстрые ответы ---

    def quick_reply_ids(self) -> list[str]:
        return quick_reply_ids(self._texts)

    def quick_reply_text(self, reply_id: str) -> str:
        """Текст заготовки — то, что получит клиент (после подписи «Поддержка:»)."""
        return self._texts.get(f"{QUICK_REPLIES_GROUP}.{reply_id}.text")

    def quick_replies_menu(self, ticket_id: int) -> Screen:
        """Меню заготовок: каждая — кнопка, текст виден заранее. Внизу — «✖️ Скрыть»."""
        ids = self.quick_reply_ids()
        items = [
            f"<b>{self._texts.get(f'{QUICK_REPLIES_GROUP}.{reply_id}.button')}</b>{LINE}"
            f"{self.quick_reply_text(reply_id)}"
            for reply_id in ids
        ]
        rows = [
            [
                InlineKeyboardButton(
                    text=self._texts.get(f"{QUICK_REPLIES_GROUP}.{reply_id}.button"),
                    callback_data=QuickReplyCallback(ticket_id=ticket_id, reply=reply_id).pack(),
                )
            ]
            for reply_id in ids
        ]
        rows.append([self._button("hide", TicketAction.HIDE, ticket_id)])
        text = PARAGRAPH.join([self._texts.get("support.quick_replies_title"), *items])
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def quick_reply_sent(self, admin_name: str, reply_id: str) -> str:
        """Что видно в теме после отправки: кто отправил и что именно."""
        return (
            self._texts.get("support.quick_reply_sent", admin=admin_name)
            + PARAGRAPH
            + self.quick_reply_text(reply_id)
        )
