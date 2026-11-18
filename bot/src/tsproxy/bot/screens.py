"""Экраны клиента: текст и кнопки. Только сборка из `content/client/`, без Telegram-вызовов."""

import logging
from dataclasses import dataclass

from aiogram.enums import ButtonStyle
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.bot.callbacks import (
    FaqCallback,
    FaqQuestion,
    InstructionsCallback,
    MenuCallback,
    Platform,
    ScreenId,
)
from tsproxy.content.dates import client_time
from tsproxy.content.loader import Texts
from tsproxy.domain.constants import TRIAL_DEVICE_LIMIT, TRIAL_TRAFFIC_GB
from tsproxy.integrations.remnawave_types import AccountStatus, ProxyAccount

logger = logging.getLogger(__name__)

# Цвет кнопок клиента (Bot API `style`, спека «Кнопки»): главная кнопка экрана — зелёная,
# опасные действия — красные, остальные — обычного цвета приложения
BUTTON_STYLES: dict[str, ButtonStyle] = {
    "buttons.connect": ButtonStyle.SUCCESS,
    "buttons.delete_device": ButtonStyle.DANGER,
    "buttons.revoke_link": ButtonStyle.DANGER,
    "buttons.confirm_revoke_link": ButtonStyle.DANGER,
}

# Telegram копирует кнопкой «Скопировать ссылку» не больше 256 символов
COPY_TEXT_MAX_LENGTH = 256

PLATFORM_TEXTS: dict[Platform, tuple[str, str]] = {
    # платформа → (текст кнопки, текст инструкции)
    Platform.IOS: ("buttons.platform_ios", "instructions.ios"),
    Platform.ANDROID: ("buttons.platform_android", "instructions.android"),
    Platform.WINDOWS: ("buttons.platform_windows", "instructions.windows"),
    Platform.MACOS: ("buttons.platform_macos", "instructions.macos"),
    Platform.LINUX: ("buttons.platform_linux", "instructions.linux"),
}


@dataclass(frozen=True)
class Screen:
    """Что показать клиенту: текст (HTML) и кнопки под ним."""

    text: str
    markup: InlineKeyboardMarkup


class Screens:
    """Собирает экраны меню из текстов."""

    def __init__(self, texts: Texts) -> None:
        self._texts = texts

    def _button(self, key: str, screen: ScreenId) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self._texts.get(key),
            callback_data=MenuCallback(screen=screen).pack(),
            style=BUTTON_STYLES.get(key),
        )

    def _back(self) -> list[InlineKeyboardButton]:
        return [self._button("buttons.back", ScreenId.MAIN)]

    def _documents(self) -> list[InlineKeyboardButton]:
        """Соглашение и политика на сайте — всегда под рукой (требование Platega)."""
        return [
            InlineKeyboardButton(
                text=self._texts.get("buttons.terms"), url=self._texts.get("documents.terms_url")
            ),
            InlineKeyboardButton(
                text=self._texts.get("buttons.privacy"),
                url=self._texts.get("documents.privacy_url"),
            ),
        ]

    def main_menu(self, *, trial_available: bool) -> Screen:
        """Главное меню. «Попробовать бесплатно» — только если пробный ещё не брал."""
        rows = [[self._button("buttons.connect", ScreenId.CONNECT)]]
        if trial_available:
            rows.append([self._button("buttons.trial", ScreenId.TRIAL)])
        rows += [
            [
                self._button("buttons.tariffs", ScreenId.TARIFFS),
                self._button("buttons.profile", ScreenId.PROFILE),
            ],
            [
                self._button("buttons.instructions", ScreenId.INSTRUCTIONS),
                self._button("buttons.support", ScreenId.SUPPORT),
            ],
            [self._button("buttons.about", ScreenId.ABOUT)],
            self._documents(),
        ]
        return Screen(self._texts.get("menu.title"), InlineKeyboardMarkup(inline_keyboard=rows))

    def about(self) -> Screen:
        return Screen(
            self._texts.get("about.text"),
            InlineKeyboardMarkup(inline_keyboard=[self._documents(), self._back()]),
        )

    def support(self) -> Screen:
        """«Поддержка»: частые вопросы кнопками, иначе — просто напишите вопрос в чат."""
        rows = [
            [
                InlineKeyboardButton(
                    text=self._texts.get(f"faq.{question.value}.button"),
                    callback_data=FaqCallback(question=question).pack(),
                )
            ]
            for question in FaqQuestion
        ]
        rows.append(self._back())
        return Screen(self._texts.get("support.intro"), InlineKeyboardMarkup(inline_keyboard=rows))

    def ticket_accepted(self) -> Screen:
        """«Обращение принято» после первого сообщения обращения — без номера, с «В меню»."""
        return Screen(self._texts.get("support.ticket_created"), self.to_menu())

    def to_menu(self) -> InlineKeyboardMarkup:
        """Одна кнопка «В меню» — под «Обращение принято» и под каждым ответом поддержки."""
        return InlineKeyboardMarkup(inline_keyboard=[self._back()])

    def faq_answer(self, question: FaqQuestion) -> Screen:
        """Ответ на частый вопрос: назад к вопросам или в меню."""
        rows = [[self._button("buttons.faq_back", ScreenId.SUPPORT)], self._back()]
        return Screen(
            self._texts.get(f"faq.{question.value}.answer"),
            InlineKeyboardMarkup(inline_keyboard=rows),
        )

    def instructions_menu(self) -> Screen:
        """Выбор платформы."""
        rows = [
            [
                InlineKeyboardButton(
                    text=self._texts.get(button_key),
                    callback_data=InstructionsCallback(platform=platform).pack(),
                )
            ]
            for platform, (button_key, _) in PLATFORM_TEXTS.items()
        ]
        rows.append(self._back())
        return Screen(
            self._texts.get("instructions.choose"), InlineKeyboardMarkup(inline_keyboard=rows)
        )

    def instructions(self, platform: Platform) -> Screen:
        """Три шага для одной платформы."""
        _, text_key = PLATFORM_TEXTS[platform]
        rows = [
            [self._button("buttons.connect", ScreenId.CONNECT)],
            [self._button("buttons.instructions_back", ScreenId.INSTRUCTIONS)],
            self._back(),
        ]
        return Screen(self._texts.get(text_key), InlineKeyboardMarkup(inline_keyboard=rows))

    def _link_rows(self, account: ProxyAccount) -> list[list[InlineKeyboardButton]]:
        """Кнопки «Открыть страницу подключения» и «Скопировать ссылку»."""
        url = account.subscription_url
        rows = [[InlineKeyboardButton(text=self._texts.get("buttons.open_subscription"), url=url)]]
        if len(url) <= COPY_TEXT_MAX_LENGTH:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self._texts.get("buttons.copy_link"),
                        copy_text=CopyTextButton(text=url),
                    )
                ]
            )
        else:
            logger.warning("Ссылка подписки длиннее %d символов: без копирования", len(url))
        return rows

    def connect(self, account: ProxyAccount) -> Screen:
        """«Подключиться»: страница подписки и копирование ссылки."""
        rows = self._link_rows(account)
        expire_at = client_time(account.expire_at)
        if account.status is AccountStatus.ACTIVE:
            text = self._texts.get("connect.active", expire_at=expire_at)
        else:
            text = self._texts.get("connect.inactive", expire_at=expire_at)
            rows.append([self._button("buttons.tariffs", ScreenId.TARIFFS)])
        rows.append([self._button("buttons.instructions", ScreenId.INSTRUCTIONS)])
        rows.append(self._back())
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def no_account(self, *, trial_available: bool) -> Screen:
        """«Подключиться», а подписки ещё нет: предложить пробный и тарифы.

        Пробный предлагаем, пока пробные включены (задача 28): без аккаунта доступа клиент
        ещё не получал, даже если пробный отмечен (панель не ответила при выдаче) —
        повторное нажатие доделает выдачу.
        """
        rows = [[self._button("buttons.trial", ScreenId.TRIAL)]] if trial_available else []
        rows += [[self._button("buttons.tariffs", ScreenId.TARIFFS)], self._back()]
        return Screen(
            self._texts.get("connect.no_account"), InlineKeyboardMarkup(inline_keyboard=rows)
        )

    def trial_granted(self, account: ProxyAccount) -> Screen:
        """Пробный период выдан: срок, лимиты и ссылка подписки."""
        rows = self._link_rows(account)
        rows += [[self._button("buttons.instructions", ScreenId.INSTRUCTIONS)], self._back()]
        text = self._texts.get(
            "trial.granted",
            expire_at=client_time(account.expire_at),
            traffic_gb=TRIAL_TRAFFIC_GB,
            device_limit=TRIAL_DEVICE_LIMIT,
        )
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def access_granted(self, text: str, account: ProxyAccount) -> Screen:
        """Доступ выдан (например, после оплаты): текст, ссылка подписки и «Инструкции»."""
        rows = self._link_rows(account)
        rows += [[self._button("buttons.instructions", ScreenId.INSTRUCTIONS)], self._back()]
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def trial_already_used(self) -> Screen:
        """Пробный уже был — предложить тарифы."""
        rows = [
            [self._button("buttons.connect", ScreenId.CONNECT)],
            [self._button("buttons.tariffs", ScreenId.TARIFFS)],
            self._back(),
        ]
        return Screen(
            self._texts.get("trial.already_used"), InlineKeyboardMarkup(inline_keyboard=rows)
        )

    def trial_pending(self) -> Screen:
        """Панель не ответила: пробный за клиентом, ссылку бот пришлёт сам."""
        return Screen(
            self._texts.get("trial.pending"), InlineKeyboardMarkup(inline_keyboard=[self._back()])
        )

    def trial_has_subscription(self) -> Screen:
        """Подписка уже есть — пробный не нужен."""
        rows = [[self._button("buttons.connect", ScreenId.CONNECT)], self._back()]
        return Screen(
            self._texts.get("trial.has_subscription"), InlineKeyboardMarkup(inline_keyboard=rows)
        )

    def trial_disabled(self) -> Screen:
        """Пробные выключены (задача 28): «временно недоступен» и тарифы."""
        rows = [[self._button("buttons.tariffs", ScreenId.TARIFFS)], self._back()]
        return Screen(self._texts.get("trial.disabled"), InlineKeyboardMarkup(inline_keyboard=rows))

    def unavailable(self) -> Screen:
        """Панель Remnawave не ответила — попросить попробовать позже."""
        return Screen(
            self._texts.get("connect.unavailable"),
            InlineKeyboardMarkup(inline_keyboard=[self._back()]),
        )

    def connect_pending(self) -> Screen:
        """«Подключиться» при сбое панели — бот пришлёт ссылку сам, когда панель ответит."""
        return Screen(
            self._texts.get("connect.pending"),
            InlineKeyboardMarkup(inline_keyboard=[self._back()]),
        )

    def error(self) -> Screen:
        """Что-то сломалось в обработчике — извиниться и вернуть в меню."""
        return Screen(
            self._texts.get("errors.generic"),
            InlineKeyboardMarkup(inline_keyboard=[self._back()]),
        )

    def help(self) -> Screen:
        """`/help` — все команды клиента."""
        return Screen(
            self._texts.get("help.text"),
            InlineKeyboardMarkup(inline_keyboard=[self._back()]),
        )

    def unknown_command(self) -> Screen:
        """Неизвестная команда. Обычные сообщения — обращения в поддержку (`bot.support`)."""
        return Screen(
            self._texts.get("fallback.unknown_command"),
            InlineKeyboardMarkup(inline_keyboard=[self._back()]),
        )
