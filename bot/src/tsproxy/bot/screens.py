"""Экраны клиента: текст и кнопки. Только сборка из `content/client/`, без Telegram-вызовов."""

import logging
from dataclasses import dataclass, replace

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
from tsproxy.content.loader import Html, Texts
from tsproxy.content.pictures import Picture
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


def link_row(texts: Texts, url: str) -> list[InlineKeyboardButton]:
    """«Подключиться» — сама ссылка подписки — и рядом «Скопировать ссылку» (спека
    bot-buttons-unify): один ряд, одно нажатие до страницы подключения."""
    row = [
        InlineKeyboardButton(
            text=texts.get("buttons.connect"),
            url=url,
            style=BUTTON_STYLES.get("buttons.connect"),
        )
    ]
    if len(url) <= COPY_TEXT_MAX_LENGTH:
        row.append(
            InlineKeyboardButton(
                text=texts.get("buttons.copy_link"), copy_text=CopyTextButton(text=url)
            )
        )
    else:
        logger.warning("Ссылка подписки длиннее %d символов: без копирования", len(url))
    return row


@dataclass(frozen=True)
class Screen:
    """Что показать клиенту: текст (HTML) и кнопки под ним.

    `keeps_text` — сообщение с текстом, который нужно сохранить (уведомление, сообщение
    об оплате): когда клиент нажимает его кнопку, бот снимает кнопки, а не удаляет
    сообщение (`ClientDelivery.show`). Обычный экран меню при нажатии удаляется.

    `picture` — экран приходит фото с подписью `text` (до 1024 символов): главное меню
    (задача 27) и «О сервисе» (задача 28); `None` — обычный текст.
    """

    text: str
    markup: InlineKeyboardMarkup
    keeps_text: bool = False
    picture: Picture | None = None

    def kept(self) -> "Screen":
        """Тот же экран как сообщение с сохраняемым текстом."""
        return replace(self, keeps_text=True)


@dataclass(frozen=True)
class ConnectButton:
    """Какой будет «Подключиться» (спека bot-buttons-unify).

    `url` — ссылка подписки: кнопка сама открывает страницу подключения, рядом
    «Скопировать ссылку». Без ссылки и `shown` — кнопка экрана «Подключиться» (бот не знает
    ссылку: аккаунта нет, панель не ответила); без ссылки и не `shown` — кнопки нет
    (доступа нет, ссылка не работает).
    """

    url: str | None = None
    shown: bool = True


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
        """«В меню» — под экраном, где действие совершено (спека bot-buttons-unify)."""
        return [self._button("buttons.back", ScreenId.MAIN)]

    def _step_back(self, screen: ScreenId) -> list[InlineKeyboardButton]:
        """«Назад» — на шаг выше, под экраном-переходом (спека bot-buttons-unify)."""
        return [self._button("buttons.step_back", screen)]

    def _link(self, key: str, url_key: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=self._texts.get(key), url=self._texts.get(url_key))

    def _documents(self) -> list[InlineKeyboardButton]:
        """Соглашение и политика на сайте — ряд кнопок в «О сервисе»."""
        return [
            self._link("buttons.terms", "documents.terms_url"),
            self._link("buttons.privacy", "documents.privacy_url"),
        ]

    def main_menu(
        self,
        *,
        trial_available: bool,
        status: str | None,
        picture: Picture,
        connect: ConnectButton,
        renew: bool,
    ) -> Screen:
        """Главное меню — фото с подписью (задача 27). «Попробовать бесплатно» — только если
        пробный ещё не брал.

        `status` — строка статуса подписки над меню (задача 19), `None` — без неё;
        `picture` — картинка по статусу подписки (`ProfileScreens.menu_header`);
        `connect` — какой будет «Подключиться»; `renew` — «Продлить подписку» вместо
        «Тарифов» (была оплаченная подписка).
        """
        rows = self._connect_rows(connect)
        if trial_available:
            rows.append([self._button("buttons.trial", ScreenId.TRIAL)])
        rows += [
            [
                self._button("buttons.renew" if renew else "buttons.tariffs", ScreenId.TARIFFS),
                self._button("buttons.profile", ScreenId.PROFILE),
            ],
            [
                self._button("buttons.instructions", ScreenId.INSTRUCTIONS),
                self._button("buttons.support", ScreenId.SUPPORT),
            ],
            [self._button("buttons.about", ScreenId.ABOUT)],
        ]
        title = self._texts.get("menu.title")
        text = title if status is None else f"{status}\n\n{title}"
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows), picture=picture)

    def _connect_rows(self, connect: ConnectButton) -> list[list[InlineKeyboardButton]]:
        if connect.url is not None:
            return [link_row(self._texts, connect.url)]
        if connect.shown:
            return [[self._button("buttons.connect", ScreenId.CONNECT)]]
        return []

    def about(self) -> Screen:
        """«О сервисе» — фото с подписью (задача 28): сайт, документы и возврат в меню."""
        rows = [
            [self._link("buttons.site", "documents.site_url")],
            self._documents(),
            self._step_back(ScreenId.MAIN),
        ]
        return Screen(
            self._texts.get("about.text"),
            InlineKeyboardMarkup(inline_keyboard=rows),
            picture=Picture.ABOUT,
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
        rows.append(self._step_back(ScreenId.MAIN))
        return Screen(self._texts.get("support.intro"), InlineKeyboardMarkup(inline_keyboard=rows))

    def ticket_accepted(self) -> Screen:
        """«Обращение принято» после первого сообщения обращения — без номера, с «В меню».
        Текст сохраняемый: при нажатии «В меню» гаснут только кнопки."""
        return Screen(self._texts.get("support.ticket_created"), self.to_menu()).kept()

    def to_menu(self) -> InlineKeyboardMarkup:
        """Одна кнопка «В меню» — под «Обращение принято» и под каждым ответом поддержки."""
        return InlineKeyboardMarkup(inline_keyboard=[self._back()])

    def faq_answer(self, question: FaqQuestion) -> Screen:
        """Ответ на частый вопрос: «Назад» — к вопросам."""
        rows = [self._step_back(ScreenId.SUPPORT)]
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
        rows.append(self._step_back(ScreenId.MAIN))
        return Screen(
            self._texts.get("instructions.choose"), InlineKeyboardMarkup(inline_keyboard=rows)
        )

    def instructions(self, platform: Platform, subscription_url: str | None) -> Screen:
        """Три шага для одной платформы.

        `subscription_url` — личная ссылка подписки клиента (задача 17): шаг «откройте вашу
        страницу подключения» ведёт прямо на неё, «Подключиться» — тоже; `None` — шаг через
        экран «Подключиться».
        """
        _, text_key = PLATFORM_TEXTS[platform]
        if subscription_url is None:
            open_page = self._texts.get("instructions.open_page_connect")
        else:
            open_page = self._texts.get("instructions.open_page_link", url=subscription_url)
        text = self._texts.get(text_key, open_page=Html(open_page))
        rows = [
            self._connect_rows(ConnectButton(url=subscription_url))[0],
            self._step_back(ScreenId.INSTRUCTIONS),
        ]
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def connect(self, account: ProxyAccount) -> Screen:
        """Экран «Подключиться» — для старых кнопок и когда меню не знало ссылку: ссылка
        в тексте и кнопкой; подписка закончилась — «Тарифы» вместо ссылки (был ли пробный,
        по аккаунту не видно)."""
        expire_at = client_time(account.expire_at)
        if account.status is AccountStatus.ACTIVE:
            text = self._texts.get(
                "connect.active", expire_at=expire_at, url=account.subscription_url
            )
            rows = [link_row(self._texts, account.subscription_url)]
        else:
            text = self._texts.get("connect.inactive", expire_at=expire_at)
            rows = [[self._button("buttons.tariffs", ScreenId.TARIFFS)]]
        rows.append([self._button("buttons.instructions", ScreenId.INSTRUCTIONS)])
        rows.append(self._step_back(ScreenId.MAIN))
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def no_account(self, *, trial_available: bool) -> Screen:
        """«Подключиться», а подписки ещё нет: предложить пробный и тарифы.

        Пробный предлагаем, пока пробные включены (задача 28): без аккаунта доступа клиент
        ещё не получал, даже если пробный отмечен (панель не ответила при выдаче) —
        повторное нажатие доделает выдачу.
        """
        rows = [[self._button("buttons.trial", ScreenId.TRIAL)]] if trial_available else []
        rows += [
            [self._button("buttons.tariffs", ScreenId.TARIFFS)],
            self._step_back(ScreenId.MAIN),
        ]
        return Screen(
            self._texts.get("connect.no_account"), InlineKeyboardMarkup(inline_keyboard=rows)
        )

    def trial_granted(self, account: ProxyAccount) -> Screen:
        """Пробный период выдан: срок, лимиты и ссылка подписки."""
        rows = [
            link_row(self._texts, account.subscription_url),
            [self._button("buttons.instructions", ScreenId.INSTRUCTIONS)],
            self._back(),
        ]
        text = self._texts.get(
            "trial.granted",
            expire_at=client_time(account.expire_at),
            traffic_gb=TRIAL_TRAFFIC_GB,
            device_limit=TRIAL_DEVICE_LIMIT,
            url=account.subscription_url,
        )
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def access_granted(self, text: str, account: ProxyAccount) -> Screen:
        """Доступ выдан (например, после оплаты): текст, ссылка подписки и «Инструкции»."""
        rows = [
            link_row(self._texts, account.subscription_url),
            [self._button("buttons.instructions", ScreenId.INSTRUCTIONS)],
            self._back(),
        ]
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))

    def refusal(self, key: str) -> Screen:
        """Отказ экраном, когда всплывашка над кнопкой опоздала (`ClientDelivery.answer`)."""
        return Screen(self._texts.get(key), InlineKeyboardMarkup(inline_keyboard=[self._back()]))

    def trial_pending(self) -> Screen:
        """Панель не ответила: пробный за клиентом, ссылку бот пришлёт сам."""
        return Screen(
            self._texts.get("trial.pending"), InlineKeyboardMarkup(inline_keyboard=[self._back()])
        )

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
