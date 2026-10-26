"""Сообщения админки (задача 18): карточка клиента, подтверждения, итоги действий.

Только сборка из `content/admin_texts.yaml`, без вызовов Telegram. Кнопки карточки
(`extend_button`, `devices_button`) можно ставить и под другими сообщениями — например,
под карточкой клиента в теме обращения (задача 17): их обрабатывает `bot.admin_clients`.
"""

from dataclasses import dataclass
from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.admin_callbacks import (
    AdminAction,
    AdminClientCallback,
    AdminDeviceCallback,
    AdminEraseCallback,
    AdminExtendCallback,
    expire_stamp,
)
from tsproxy.content.loader import Texts
from tsproxy.db.models import Client
from tsproxy.domain.constants import MAX_MANUAL_GRANT_DAYS
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.domain.profile import ProfileStatus, format_gigabytes
from tsproxy.integrations.remnawave_types import Device
from tsproxy.services.admin import (
    ClientCard,
    ExtensionPreview,
    PanelInfo,
    PaymentInfo,
    TrialInfo,
)
from tsproxy.services.profile import DeviceList, device_ref

LINE = "\n"
PARAGRAPH = "\n\n"
# Сколько дней дают кнопки «➕» под карточкой; любое другое число — командой /give
EXTEND_PRESETS = (1, 7, 30)
# Кнопок «🗑» в ряду: без личного лимита устройств их может быть много
DEVICE_BUTTONS_PER_ROW = 5
# Модель и система устройства приходят от приложения клиента — длинные обрезаем
DEVICE_TEXT_MAX_LENGTH = 64

CARD_STATUS_TEXTS: dict[ProfileStatus, str] = {
    ProfileStatus.NO_SUBSCRIPTION: "admin.card.no_account",
    ProfileStatus.TRIAL: "admin.card.status_trial",
    ProfileStatus.TRIAL_TRAFFIC_USED_UP: "admin.card.status_trial_traffic_used_up",
    ProfileStatus.ACTIVE: "admin.card.status_active",
    ProfileStatus.GRACE: "admin.card.status_grace",
    ProfileStatus.EXPIRED: "admin.card.status_expired",
    ProfileStatus.SUSPENDED: "admin.card.status_suspended",
}


@dataclass(frozen=True)
class AdminScreen:
    """Сообщение в группе администраторов. `markup` — None, если кнопок нет."""

    text: str
    markup: InlineKeyboardMarkup | None = None


def _keyboard(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def extend_button(texts: Texts, telegram_id: int, days: int) -> InlineKeyboardButton:
    """«➕ N дн.» — откроет предпросмотр выдачи с подтверждением."""
    return InlineKeyboardButton(
        text=texts.get("admin.buttons.extend", days=days),
        callback_data=AdminExtendCallback(telegram_id=telegram_id, days=days).pack(),
    )


def devices_button(texts: Texts, telegram_id: int) -> InlineKeyboardButton:
    """«📱 Устройства» — список устройств клиента с «🗑» у каждого."""
    return _action_button(texts, "admin.buttons.devices", AdminAction.DEVICES, telegram_id)


def erase_button(texts: Texts, telegram_id: int) -> InlineKeyboardButton:
    """«🗑 Удалить данные» — предпросмотр удаления с подтверждением (задача 24)."""
    return InlineKeyboardButton(
        text=texts.get("admin.buttons.erase"),
        callback_data=AdminEraseCallback(telegram_id=telegram_id).pack(),
    )


def _action_button(
    texts: Texts, key: str, action: AdminAction, telegram_id: int, **values: object
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=texts.get(key, **values),
        callback_data=AdminClientCallback(action=action, telegram_id=telegram_id).pack(),
    )


class AdminScreens:
    """Собирает сообщения админки из текстов."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _text(self, key: str, **values: object) -> AdminScreen:
        return AdminScreen(self._texts.get(key, **values))

    def _cancel(self, telegram_id: int) -> InlineKeyboardButton:
        return _action_button(self._texts, "admin.buttons.cancel", AdminAction.CANCEL, telegram_id)

    def _or_none(self, value: str | None) -> str:
        return value or self._texts.get("admin.no_value")

    # --- Простые ответы ---

    def usage(self) -> AdminScreen:
        return self._text("admin.usage", max_days=MAX_MANUAL_GRANT_DAYS)

    def give_usage(self) -> AdminScreen:
        return self._text("admin.give_usage", max_days=MAX_MANUAL_GRANT_DAYS)

    def days_out_of_range(self) -> AdminScreen:
        return self._text("admin.days_out_of_range", max_days=MAX_MANUAL_GRANT_DAYS)

    def not_found(self, query: str) -> AdminScreen:
        return self._text("admin.not_found", query=query)

    def client_not_found(self, telegram_id: int) -> AdminScreen:
        return self._text("admin.client_not_found", telegram_id=telegram_id)

    def panel_unavailable(self) -> AdminScreen:
        return self._text("admin.panel_unavailable")

    def cancelled(self) -> AdminScreen:
        return self._text("admin.cancelled")

    def not_admin(self) -> str:
        return self._texts.get("admin.not_admin")

    def many_found(self, clients: list[Client]) -> AdminScreen:
        items = [
            self._texts.get(
                "admin.many_found_item",
                name=self._or_none(c.name),
                telegram_id=c.telegram_id,
            )
            for c in clients
        ]
        return AdminScreen(LINE.join([self._texts.get("admin.many_found"), *items]))

    # --- Карточка ---

    def card(self, card: ClientCard) -> AdminScreen:
        client = card.client
        header = self._texts.get(
            "admin.card.title",
            name=self._or_none(client.name),
            telegram_id=client.telegram_id,
            username=self._or_none(client.username),
            first_seen_at=format_moscow(client.first_seen_at),
        )
        if client.bot_blocked_at is not None:
            blocked = self._texts.get("admin.card.blocked", at=format_moscow(client.bot_blocked_at))
            header = LINE.join([header, blocked])
        state = [self._panel(card.panel)]
        if client.grace_started_at is not None:
            state.append(
                self._texts.get("admin.card.grace", at=format_moscow(client.grace_started_at))
            )
        state.append(self._trial(card.trial))
        parts = [header, LINE.join(state), self._payments(card)]
        return AdminScreen(PARAGRAPH.join(parts), self._card_keyboard(client.telegram_id))

    def _panel(self, panel: PanelInfo | None) -> str:
        if panel is None:
            return self._texts.get("admin.card.panel_unavailable")
        if panel.profile is None:
            return self._texts.get("admin.card.no_account")
        profile = panel.profile
        lines = [
            self._texts.get(
                CARD_STATUS_TEXTS[profile.status],
                expire_at=format_moscow(profile.expire_at),
            )
        ]
        if profile.traffic is not None:
            lines.append(
                self._texts.get(
                    "admin.card.traffic",
                    used=format_gigabytes(profile.traffic.used_bytes),
                    limit=format_gigabytes(profile.traffic.limit_bytes),
                )
            )
        slots = profile.devices
        if slots.limit is None:
            lines.append(self._texts.get("admin.card.devices_no_limit", used=slots.used))
        else:
            lines.append(self._texts.get("admin.card.devices", used=slots.used, limit=slots.limit))
        return LINE.join(lines)

    def _trial(self, trial: TrialInfo) -> str:
        if trial.granted_at is None:
            return self._texts.get("admin.card.trial_none")
        key = "admin.card.trial_delivered" if trial.delivered else "admin.card.trial_pending"
        return self._texts.get(key, at=format_moscow(trial.granted_at), extensions=trial.extensions)

    def _payments(self, card: ClientCard) -> str:
        payments = card.payments
        if payments.count == 0:
            return self._texts.get("admin.card.payments_none")
        title = self._texts.get(
            "admin.card.payments", count=payments.count, total=format_rubles(payments.total)
        )
        return LINE.join([title, *(self._payment(p) for p in payments.recent)])

    def _payment(self, payment: PaymentInfo) -> str:
        return self._texts.get(
            "admin.card.payment_item",
            paid_at=format_moscow(payment.paid_at),
            tariff=payment.tariff_id,
            days=payment.days,
            amount=format_rubles(payment.amount),
            status=self._texts.get(f"admin.order_status.{payment.status.value}"),
        )

    def _card_keyboard(self, telegram_id: int) -> InlineKeyboardMarkup:
        texts = self._texts
        return _keyboard(
            [extend_button(texts, telegram_id, days) for days in EXTEND_PRESETS],
            [
                devices_button(texts, telegram_id),
                _action_button(
                    texts, "admin.buttons.reset_trial", AdminAction.RESET_TRIAL_ASK, telegram_id
                ),
            ],
            [_action_button(texts, "admin.buttons.revoke", AdminAction.REVOKE_ASK, telegram_id)],
            [erase_button(texts, telegram_id)],  # задача 24
        )

    # --- Ручная выдача ---

    def extend_preview(self, client: Client, preview: ExtensionPreview) -> AdminScreen:
        current = (
            self._texts.get("admin.extend.no_account")
            if preview.current_expire_at is None
            else format_moscow(preview.current_expire_at)
        )
        text = self._texts.get(
            "admin.extend.preview",
            name=self._or_none(client.name),
            telegram_id=preview.telegram_id,
            days=preview.days,
            current=current,
            new=format_moscow(preview.new_expire_at),
        )
        confirm = InlineKeyboardButton(
            text=self._texts.get("admin.buttons.confirm_extend", days=preview.days),
            callback_data=AdminExtendCallback(
                telegram_id=preview.telegram_id,
                days=preview.days,
                confirm=True,
                stamp=expire_stamp(preview.current_expire_at),
            ).pack(),
        )
        return AdminScreen(text, _keyboard([confirm], [self._cancel(preview.telegram_id)]))

    def extend_done(self, telegram_id: int, days: int, expire_at: datetime) -> AdminScreen:
        return self._text(
            "admin.extend.done",
            telegram_id=telegram_id,
            days=days,
            expire_at=format_moscow(expire_at),
        )

    def extend_changed(self, telegram_id: int, current: datetime | None) -> AdminScreen:
        shown = (
            self._texts.get("admin.extend.no_account")
            if current is None
            else format_moscow(current)
        )
        return self._text("admin.extend.changed", telegram_id=telegram_id, current=shown)

    # --- Вопросы с подтверждением ---

    def _ask(
        self, key: str, confirm_key: str, action: AdminAction, telegram_id: int
    ) -> AdminScreen:
        confirm = _action_button(self._texts, confirm_key, action, telegram_id)
        return AdminScreen(
            self._texts.get(key, telegram_id=telegram_id),
            _keyboard([confirm], [self._cancel(telegram_id)]),
        )

    def reset_trial_ask(self, telegram_id: int) -> AdminScreen:
        return self._ask(
            "admin.reset.ask", "admin.buttons.confirm_reset", AdminAction.RESET_TRIAL, telegram_id
        )

    def revoke_ask(self, telegram_id: int) -> AdminScreen:
        return self._ask(
            "admin.revoke.ask", "admin.buttons.confirm_revoke", AdminAction.REVOKE, telegram_id
        )

    def result(self, key: str, telegram_id: int) -> AdminScreen:
        """Итог действия: `admin.reset.done`, `admin.revoke.no_account` и т. п."""
        return self._text(key, telegram_id=telegram_id)

    # --- Устройства ---

    def devices(
        self, telegram_id: int, devices: DeviceList, *, notice_key: str | None = None
    ) -> AdminScreen:
        notice = [] if notice_key is None else [self._texts.get(notice_key)]
        if not devices.devices:
            empty = self._texts.get("admin.devices.empty", telegram_id=telegram_id)
            return AdminScreen(PARAGRAPH.join([empty, *notice]))
        used = len(devices.devices)
        if devices.limit is None:
            title = self._texts.get(
                "admin.devices.title_no_limit", telegram_id=telegram_id, used=used
            )
        else:
            title = self._texts.get(
                "admin.devices.title", telegram_id=telegram_id, used=used, limit=devices.limit
            )
        items = [self._device(n, d) for n, d in enumerate(devices.devices, start=1)]
        buttons = [
            InlineKeyboardButton(
                text=self._texts.get("admin.buttons.delete_device", number=number),
                callback_data=AdminDeviceCallback(
                    telegram_id=telegram_id, ref=device_ref(device.hwid)
                ).pack(),
            )
            for number, device in enumerate(devices.devices, start=1)
        ]
        rows = [
            buttons[start : start + DEVICE_BUTTONS_PER_ROW]
            for start in range(0, len(buttons), DEVICE_BUTTONS_PER_ROW)
        ]
        text = PARAGRAPH.join([title, *notice, LINE.join(items)])
        return AdminScreen(text, _keyboard(*rows))

    def _device(self, number: int, device: Device) -> str:
        unknown = self._texts.get("admin.devices.unknown")
        system = " ".join(part for part in (device.platform, device.os_version) if part)
        return self._texts.get(
            "admin.devices.item",
            number=number,
            name=(device.device_model or unknown)[:DEVICE_TEXT_MAX_LENGTH],
            system=(system or unknown)[:DEVICE_TEXT_MAX_LENGTH],
            connected_at=format_moscow(device.created_at),
        )
