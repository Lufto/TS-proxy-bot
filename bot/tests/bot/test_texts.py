"""Тексты экранов: все на месте, без слова «VPN», влезают в ограничения Telegram."""

import re
import shutil
import unicodedata
from pathlib import Path

import pytest
import yaml
from conftest import REPO_CONFIG, REPO_CONTENT

from tsproxy.bot.callbacks import (
    FaqCallback,
    FaqQuestion,
    InstructionsCallback,
    MenuCallback,
    Platform,
    QuickReplyCallback,
    ScreenId,
    TicketAction,
    TicketCallback,
)
from tsproxy.bot.screens import ConnectButton, Screens
from tsproxy.content.loader import (
    QUICK_REPLY_ID_MAX_LENGTH,
    REQUIRED_TEXTS,
    Content,
    ContentError,
    Texts,
    load_content,
    quick_reply_ids,
)
from tsproxy.content.pictures import Picture
from tsproxy.integrations.fakes import FakeRemnawave

# Telegram: текст всплывающего ответа на кнопку — до 200 символов, данные кнопки — до 64 байт
CALLBACK_ANSWER_MAX = 200
CALLBACK_DATA_MAX_BYTES = 64
FORBIDDEN_WORDS = re.compile(r"vpn|впн", re.IGNORECASE)
# Кнопки клиента — без смайликов (спека «Кнопки»): кнопка начинается с буквы или цифры
CLIENT_BUTTON = re.compile(r"^buttons\.|^faq\..+\.button$")
# Цитата кнопки со смайликом в начале: «Подключиться»
# Знаки, которые делают символ эмодзи: VS16 («ℹ️») и склейка (ZWJ)
EMOJI_MODIFIERS = frozenset("\ufe0f\u200d")
# Эмодзи, которые Unicode считает буквами
EMOJI_LETTERS = frozenset("\u2139")  # ℹ
# ⭐ после суммы — знак валюты (звёзды Telegram), а не иконка кнопки
CURRENCY_SIGNS = frozenset("⭐")
EMOJI_QUOTE = re.compile(r"«[^\w\s«»]+\s+\w[^»]*»")
REPO_ROOT = REPO_CONTENT.parent.parent
# Где кнопки бота цитируются клиенту: тексты бота и страницы сайта (старый и новый)
QUOTING_FILES = sorted(
    [
        *(REPO_CONTENT / "client").rglob("*.yaml"),
        *(REPO_ROOT / "site").rglob("*.html"),
        *(REPO_ROOT / "web" / "apiStatic").rglob("*.ts"),
    ]
)
# Инструкция — самая короткая выжимка (задача 16): было до ~1300 знаков у iPhone
INSTRUCTION_MAX_CHARS = 400
HTML_TAG = re.compile(r"<[^>]+>")
# Тексты с подстановками; у остальных обязательных текстов подстановок нет
TEXT_PLACEHOLDERS: dict[str, set[str]] = {
    "connect.active": {"expire_at", "url"},
    "connect.inactive": {"expire_at"},
    "trial.granted": {"expire_at", "traffic_gb", "device_limit", "url"},
    # задача 17: шаг «откройте вашу страницу подключения» в инструкциях
    **{f"instructions.{platform.value}": {"open_page"} for platform in Platform},
    "instructions.open_page_link": {"url"},
    # задача 12: тарифы и оплата
    "buttons.pay": {"price"},
    "tariffs.button": {"title", "price"},
    "tariffs.button_discount": {"title", "price", "discount"},
    "payment.description": {"title"},
    "payment.screen": {"title", "price", "expires_at"},
    # задача 13: оплата засчитана
    "payment.paid": {"expire_at", "device_limit", "url"},
    "payment.paid_prorated": {"expire_at", "device_limit", "amount", "price", "url"},
    "payment.disputed": {"expire_at"},
    # задача 21: сообщение о возврате, перенос подписки
    "refund.completed": {"amount", "expire_at"},
    "refund.completed_access_ended": {"amount"},
    "transfer.received": {"expire_at"},
    "manual_grant.added": {"days", "expire_at"},
    # задача 33: оплата звёздами Telegram
    "buttons.pay_sbp": {"price"},
    "buttons.pay_stars": {"stars"},
    "units.rubles": {"amount"},
    "units.stars": {"stars"},
    "tariffs.screen": {"title", "price", "stars"},
    "stars.invoice_title": {"title"},
    "stars.invoice_description": {"title"},
    "stars.price_label": {"title"},
    "stars.invoice_sent": {"stars"},
    "stars.pay_button": {"stars"},
    "stars.returned": {"stars"},
    "stars.return_failed": {"stars"},
    "stars.unrecorded": {"stars"},
    # задача 08: профиль и устройства
    "buttons.delete_device": {"number"},
    "profile.status_trial": {"expire_at", "left"},
    "profile.status_trial_traffic_used_up": set(),
    "profile.status_active": {"expire_at", "left"},
    "menu.status_trial": {"expire_at"},
    "menu.status_active": {"expire_at"},
    "menu.status_grace": {"expire_at"},
    "profile.status_grace": {"expire_at", "left"},
    "profile.status_expired": {"expire_at"},
    "profile.devices": {"used", "limit"},
    "profile.devices_no_limit": {"used"},
    "profile.traffic": {"used", "limit"},
    "devices.title": {"used", "limit"},
    "devices.title_no_limit": {"used"},
    "devices.item": {"number", "name", "system", "connected_at"},
    "devices.item_no_system": {"number", "name", "connected_at"},
    "revoke_link.too_soon": {"available_at"},
    "revoke_link.available_at": {"available_at"},
    # задача 11: льготный период
    "grace.started": {"until"},
    "grace.reminder": {"until"},
    "grace.last_day": {"until"},
    # задача 10: напоминания, продления пробного, пробный трафик
    "notices.trial_day_before": {"expire_at"},
    "notices.trial_hour_before": {"expire_at"},
    "notices.paid_day_before": {"expire_at"},
    "notices.paid_hour_before": {"expire_at"},
    "notices.trial_extended": {"days", "expire_at"},
    "notices.trial_traffic_warning": {"used", "limit"},
    "notices.trial_traffic_used_up": {"limit"},
    "compensation.extended": {"amount", "until"},
    "compensation.grace_extended": {"amount", "until"},
    **{
        f"units.{unit}_{form}": {"n"}
        for unit in ("days", "hours")
        for form in ("one", "few", "many")
    },
}


def test_missing_required_text_stops_startup(tmp_path: Path) -> None:
    shutil.copytree(REPO_CONTENT, tmp_path, dirs_exist_ok=True)
    texts_path = tmp_path / "client" / "connect.yaml"
    data = yaml.safe_load(texts_path.read_text(encoding="utf-8"))
    del data["connect"]["no_account"]
    texts_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ContentError, match=r"client/.*connect\.no_account"):
        load_content(tmp_path, REPO_CONFIG)


def test_missing_picture_stops_startup(tmp_path: Path) -> None:
    shutil.copytree(REPO_CONTENT, tmp_path, dirs_exist_ok=True)
    (tmp_path / "images" / "menu-grace.png").unlink()

    with pytest.raises(ContentError, match=r"menu-grace\.png"):
        load_content(tmp_path, REPO_CONFIG)


def _edit_menu_texts(content_dir: Path, **changes: str) -> None:
    """Поменять тексты `client/menu.yaml`: `group__key="…"` — ключ `group.key`."""
    path = content_dir / "client" / "menu.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name, text in changes.items():
        group, key = name.split("__")
        data[group][key] = text
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_too_long_about_caption_stops_startup(tmp_path: Path) -> None:
    """Подпись под фото — до 1024 символов: длинная сломала бы «О сервисе» у клиента."""
    shutil.copytree(REPO_CONTENT, tmp_path, dirs_exist_ok=True)
    _edit_menu_texts(tmp_path, about__text="а" * 1025)

    with pytest.raises(ContentError, match=r"about\.text"):
        load_content(tmp_path, REPO_CONFIG)


def test_menu_caption_with_longest_status_line_must_fit(tmp_path: Path) -> None:
    """Подпись меню — строка статуса и заголовок: каждый по отдельности влезает, вместе — нет."""
    shutil.copytree(REPO_CONTENT, tmp_path, dirs_exist_ok=True)
    _edit_menu_texts(
        tmp_path, menu__title="а" * 600, menu__status_grace="⛔ " + "б" * 400 + " {expire_at}"
    )

    with pytest.raises(ContentError, match=r"menu\.status_grace"):
        load_content(tmp_path, REPO_CONFIG)


@pytest.mark.parametrize(
    "path", sorted(REPO_CONTENT.rglob("*.yaml")), ids=lambda path: f"{path.parent.name}/{path.name}"
)
def test_no_vpn_word_in_texts(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert FORBIDDEN_WORDS.findall(text) == []


def has_emoji(text: str) -> bool:
    """Эмодзи — символ-картинка (`So`), знак «показать как эмодзи» (VS16) или склейка (ZWJ).
    «ℹ» Unicode считает буквой, поэтому `isalnum` его пропускает."""
    return any(
        (unicodedata.category(char) == "So" and char not in CURRENCY_SIGNS)
        or char in EMOJI_MODIFIERS
        or char in EMOJI_LETTERS
        for char in text
    )


def test_emoji_detection_catches_letter_like_emoji() -> None:
    assert has_emoji("ℹ️ О сервисе")
    assert has_emoji("ℹ О сервисе")
    assert has_emoji("🚀 Подключиться")
    assert not has_emoji("Подключиться")
    assert not has_emoji("« Назад")
    assert not has_emoji("Stars — 270 ⭐")


def test_client_buttons_have_no_emoji(content: Content) -> None:
    buttons = {key: text for key, text in content.texts._items.items() if CLIENT_BUTTON.search(key)}
    assert "buttons.connect" in buttons
    assert {key: text for key, text in buttons.items() if has_emoji(text)} == {}


def test_do_not_forward_warning_only_on_connect_screen(content: Content) -> None:
    """«Не пересылайте ссылку» — один раз, там, где клиент получает ссылку (спека «Тексты»)."""
    warned = [key for key, text in content.texts._items.items() if "Не пересылайте" in text]
    assert warned == ["connect.active"]


@pytest.mark.parametrize("path", QUOTING_FILES, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_buttons_are_quoted_without_emoji(path: Path) -> None:
    """«нажмите „Подключиться“», а не старое «Подключиться»."""
    assert EMOJI_QUOTE.findall(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("platform", list(Platform))
@pytest.mark.parametrize("subscription_url", [None, "https://sub.ts-proxy.com/abc"])
def test_instructions_are_short(
    content: Content, platform: Platform, subscription_url: str | None
) -> None:
    """С личной ссылкой и без неё (задача 17)."""
    text = Screens(content.texts).instructions(platform, subscription_url).text
    assert len(HTML_TAG.sub("", text)) <= INSTRUCTION_MAX_CHARS


async def test_all_screens_use_only_required_texts(content: Content) -> None:
    """Если экран берёт текст, которого нет в REQUIRED_TEXTS, бот упал бы у клиента, а не при
    запуске. Собираем все экраны из текстов, урезанных до обязательных."""
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    screens = Screens(texts)
    remnawave = FakeRemnawave()
    account = await remnawave.get_or_create_account(
        1, expire_at=remnawave._now(), device_limit=3, traffic_limit_bytes=0
    )
    screens.main_menu(
        trial_available=True,
        status=None,
        picture=Picture.MENU_NEW,
        connect=ConnectButton(),
        renew=False,
    )
    screens.main_menu(
        trial_available=False,
        status=None,
        picture=Picture.MENU_ACTIVE,
        connect=ConnectButton(url=account.subscription_url),
        renew=True,
    )
    screens.about()
    screens.support()
    for question in FaqQuestion:
        screens.faq_answer(question)
    screens.instructions_menu()
    for platform in Platform:
        screens.instructions(platform, None)
        screens.instructions(platform, account.subscription_url)
    screens.connect(account)
    screens.no_account(trial_available=True)
    screens.trial_granted(account)
    screens.trial_pending()
    screens.unavailable()
    screens.error()
    screens.unknown_command()


def test_texts_placeholders_are_expected(content: Content) -> None:
    for key in REQUIRED_TEXTS:
        expected = TEXT_PLACEHOLDERS.get(key, set())
        assert content.texts.placeholders(key) == expected, key


@pytest.mark.parametrize(
    "key",
    [
        "throttle.callback",
        "errors.stale_button",
        # задача 20 (bot-telegram-ui): отказы пробного — всплывашкой
        "trial.already_used",
        "trial.has_subscription",
        "trial.disabled",
    ],
)
def test_callback_answers_fit_telegram_limit(content: Content, key: str) -> None:
    text = content.texts.get(key)
    assert len(text) <= CALLBACK_ANSWER_MAX
    assert "<" not in text  # всплывашка показывает текст как есть, без разметки


def test_callback_data_fits_telegram_limit(content: Content) -> None:
    packed = [MenuCallback(screen=s).pack() for s in ScreenId]
    packed += [InstructionsCallback(platform=p).pack() for p in Platform]
    packed += [FaqCallback(question=q).pack() for q in FaqQuestion]
    big_ticket = 10**12  # номер обращения с запасом
    packed += [TicketCallback(action=a, ticket_id=big_ticket).pack() for a in TicketAction]
    packed += [
        QuickReplyCallback(ticket_id=big_ticket, reply=reply).pack()
        for reply in [*quick_reply_ids(content.admin_texts), "x" * QUICK_REPLY_ID_MAX_LENGTH]
    ]
    assert all(len(data.encode()) <= CALLBACK_DATA_MAX_BYTES for data in packed)


@pytest.mark.parametrize(
    "key",
    [
        "support.already_yours",
        "support.already_closed",
        "support.take_closed",
        "support.ticket_not_found",
        "support.quick_reply_not_found",
    ],
)
def test_admin_callback_answers_fit_telegram_limit(content: Content, key: str) -> None:
    assert len(content.admin_texts.get(key)) <= CALLBACK_ANSWER_MAX
