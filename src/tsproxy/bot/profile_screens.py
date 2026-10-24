"""Экраны «👤 Мой профиль» и «📱 Мои устройства»: текст и кнопки из `content/texts.yaml`.

Без UUID, HWID, имени аккаунта в панели и других технических кодов (задача 10).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.callbacks import DeviceCallback, MenuCallback, ScreenId
from tsproxy.bot.screens import Screen
from tsproxy.content.loader import Texts
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.profile import ProfileStatus, TimeLeft, format_gigabytes, plural_form
from tsproxy.integrations.remnawave_types import Device
from tsproxy.services.profile import DeviceList, Profile, device_ref

# Части экрана разделяем пустой строкой
PARAGRAPH = "\n\n"
# Модель устройства присылает приложение клиента — длинную обрезаем
DEVICE_NAME_MAX_LENGTH = 64

STATUS_TEXTS: dict[ProfileStatus, str] = {
    ProfileStatus.TRIAL: "profile.status_trial",
    ProfileStatus.TRIAL_TRAFFIC_USED_UP: "profile.status_trial_traffic_used_up",
    ProfileStatus.ACTIVE: "profile.status_active",
    ProfileStatus.GRACE: "profile.status_grace",
    ProfileStatus.EXPIRED: "profile.status_expired",
    ProfileStatus.SUSPENDED: "profile.status_suspended",
}

# Куда ведёт кнопка под статусом: продлить, выбрать тариф или написать в поддержку
STATUS_ACTIONS: dict[ProfileStatus, tuple[str, ScreenId]] = {
    ProfileStatus.TRIAL: ("buttons.tariffs", ScreenId.TARIFFS),
    ProfileStatus.TRIAL_TRAFFIC_USED_UP: ("buttons.tariffs", ScreenId.TARIFFS),
    ProfileStatus.ACTIVE: ("buttons.renew", ScreenId.TARIFFS),
    ProfileStatus.GRACE: ("buttons.renew", ScreenId.TARIFFS),
    ProfileStatus.EXPIRED: ("buttons.renew", ScreenId.TARIFFS),
    ProfileStatus.SUSPENDED: ("buttons.support", ScreenId.SUPPORT),
}


class ProfileScreens:
    """Собирает экраны профиля и устройств из текстов."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _button(self, key: str, screen: ScreenId) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self._texts.get(key), callback_data=MenuCallback(screen=screen).pack()
        )

    def _back(self) -> list[InlineKeyboardButton]:
        return [self._button("buttons.back", ScreenId.MAIN)]

    def _count(self, unit: str, number: int) -> str:
        return self._texts.get(f"units.{unit}_{plural_form(number).value}", n=number)

    def _left(self, left: TimeLeft) -> str:
        """«5 дней 3 часа», «5 дней», «3 часа» или «меньше часа»."""
        parts = []
        if left.days:
            parts.append(self._count("days", left.days))
        if left.hours:
            parts.append(self._count("hours", left.hours))
        return " ".join(parts) or self._texts.get("units.less_than_hour")

    def profile(self, profile: Profile) -> Screen:
        """«👤 Мой профиль»: статус, срок и остаток, устройства, трафик в пробном."""
        status = self._texts.get(
            STATUS_TEXTS[profile.status],
            expire_at=format_moscow(profile.expire_at),
            left=self._left(profile.time_left),
        )
        slots = profile.devices
        if slots.limit is None:
            details = [self._texts.get("profile.devices_no_limit", used=slots.used)]
        else:
            details = [self._texts.get("profile.devices", used=slots.used, limit=slots.limit)]
        if profile.traffic is not None:
            details.append(
                self._texts.get(
                    "profile.traffic",
                    used=format_gigabytes(profile.traffic.used_bytes),
                    limit=format_gigabytes(profile.traffic.limit_bytes),
                )
            )
        text = PARAGRAPH.join([self._texts.get("profile.title"), status, "\n".join(details)])
        action_key, action_screen = STATUS_ACTIONS[profile.status]
        rows = [
            [self._button("buttons.devices", ScreenId.DEVICES)],
            [self._button(action_key, action_screen)],
            [self._button("buttons.connect", ScreenId.CONNECT)],
            self._back(),
        ]
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def no_account(self) -> Screen:
        """Профиль, когда подписки ещё нет: предложить пробный и тарифы.

        Пробный — всегда, как в «🚀 Подключиться» (`Screens.no_account`): без аккаунта
        доступа клиент не получал, даже если пробный отмечен.
        """
        rows = [
            [self._button("buttons.trial", ScreenId.TRIAL)],
            [self._button("buttons.tariffs", ScreenId.TARIFFS)],
            self._back(),
        ]
        return Screen(
            self._texts.get("profile.no_account"), InlineKeyboardMarkup(inline_keyboard=rows)
        )

    def _device(self, number: int, device: Device) -> str:
        name = (device.device_model or self._texts.get("devices.unknown_model"))[
            :DEVICE_NAME_MAX_LENGTH
        ]
        system = " ".join(part for part in (device.platform, device.os_version) if part)
        connected_at = format_moscow(device.created_at)
        if not system:
            return self._texts.get(
                "devices.item_no_system", number=number, name=name, connected_at=connected_at
            )
        return self._texts.get(
            "devices.item",
            number=number,
            name=name,
            system=system[:DEVICE_NAME_MAX_LENGTH],
            connected_at=connected_at,
        )

    def devices(self, devices: DeviceList, *, notice_key: str | None = None) -> Screen:
        """«📱 Мои устройства»: список и «🗑 Удалить» у каждого.

        `notice_key` — итог удаления («удалено» или «уже нет») над списком.
        """
        notice = [] if notice_key is None else [self._texts.get(notice_key)]
        back_rows = [[self._button("buttons.profile_back", ScreenId.PROFILE)], self._back()]
        if not devices.devices:
            text = PARAGRAPH.join([self._texts.get("devices.empty"), *notice])
            rows = [[self._button("buttons.connect", ScreenId.CONNECT)], *back_rows]
            return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))
        used = len(devices.devices)
        if devices.limit is None:
            title = self._texts.get("devices.title_no_limit", used=used)
        else:
            title = self._texts.get("devices.title", used=used, limit=devices.limit)
        items = [self._device(number, d) for number, d in enumerate(devices.devices, start=1)]
        text = PARAGRAPH.join([title, *notice, *items, self._texts.get("devices.hint")])
        rows = [
            [
                InlineKeyboardButton(
                    text=self._texts.get("buttons.delete_device", number=number),
                    callback_data=DeviceCallback(ref=device_ref(device.hwid)).pack(),
                )
            ]
            for number, device in enumerate(devices.devices, start=1)
        ]
        rows += back_rows
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))
