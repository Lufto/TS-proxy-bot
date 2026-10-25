"""Тексты экранов: все на месте, без слова «VPN», влезают в ограничения Telegram."""

import re
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import REPO_CONTENT

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
from tsproxy.bot.screens import Screens
from tsproxy.content.loader import (
    ADMIN_TEXTS_FILE,
    QUICK_REPLY_ID_MAX_LENGTH,
    REQUIRED_TEXTS,
    TEXTS_FILE,
    Content,
    ContentError,
    Texts,
    load_content,
    quick_reply_ids,
)
from tsproxy.integrations.fakes import FakeRemnawave

# Telegram: текст всплывающего ответа на кнопку — до 200 символов, данные кнопки — до 64 байт
CALLBACK_ANSWER_MAX = 200
CALLBACK_DATA_MAX_BYTES = 64
FORBIDDEN_WORDS = re.compile(r"vpn|впн", re.IGNORECASE)
# Тексты с подстановками; у остальных обязательных текстов подстановок нет
TEXT_PLACEHOLDERS: dict[str, set[str]] = {
    "connect.active": {"expire_at"},
    "connect.inactive": {"expire_at"},
    "trial.granted": {"expire_at", "traffic_gb", "device_limit"},
    "support.ticket_created": {"ticket_id"},
    # задача 12: тарифы и оплата
    "buttons.pay": {"price"},
    "tariffs.button": {"title", "price"},
    "tariffs.button_discount": {"title", "price", "discount"},
    "tariffs.button_hidden": {"title", "price"},
    "payment.description": {"title"},
    "payment.screen": {"title", "price", "expires_at"},
    # задача 13: оплата засчитана
    "payment.paid": {"expire_at", "device_limit"},
    "payment.paid_prorated": {"expire_at", "device_limit", "amount", "price"},
    # задача 08: профиль и устройства
    "buttons.delete_device": {"number"},
    "profile.status_trial": {"expire_at", "left"},
    "profile.status_trial_traffic_used_up": set(),
    "profile.status_active": {"expire_at", "left"},
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
    **{
        f"units.{unit}_{form}": {"n"}
        for unit in ("days", "hours")
        for form in ("one", "few", "many")
    },
}


def test_missing_required_text_stops_startup(tmp_path: Path) -> None:
    shutil.copytree(REPO_CONTENT, tmp_path, dirs_exist_ok=True)
    texts_path = tmp_path / TEXTS_FILE
    data = yaml.safe_load(texts_path.read_text(encoding="utf-8"))
    del data["connect"]["no_account"]
    texts_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ContentError, match=r"connect\.no_account"):
        load_content(tmp_path)


@pytest.mark.parametrize("file_name", [TEXTS_FILE, ADMIN_TEXTS_FILE])
def test_no_vpn_word_in_texts(file_name: str) -> None:
    text = (REPO_CONTENT / file_name).read_text(encoding="utf-8")
    assert FORBIDDEN_WORDS.findall(text) == []


async def test_all_screens_use_only_required_texts(content: Content) -> None:
    """Если экран берёт текст, которого нет в REQUIRED_TEXTS, бот упал бы у клиента, а не при
    запуске. Собираем все экраны из текстов, урезанных до обязательных."""
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    screens = Screens(texts)
    remnawave = FakeRemnawave()
    account = await remnawave.get_or_create_account(
        1, expire_at=remnawave._now(), device_limit=3, traffic_limit_bytes=0
    )
    screens.main_menu(trial_available=True)
    screens.about()
    screens.support()
    for question in FaqQuestion:
        screens.faq_answer(question)
    screens.instructions_menu()
    for platform in Platform:
        screens.instructions(platform)
    screens.connect(account)
    screens.no_account()
    screens.trial_granted(account)
    screens.trial_already_used()
    screens.trial_pending()
    screens.trial_has_subscription()
    screens.unavailable()
    screens.error()
    screens.unknown_command()


def test_texts_placeholders_are_expected(content: Content) -> None:
    for key in REQUIRED_TEXTS:
        expected = TEXT_PLACEHOLDERS.get(key, set())
        assert content.texts.placeholders(key) == expected, key


@pytest.mark.parametrize("key", ["throttle.callback", "errors.stale_button"])
def test_callback_answers_fit_telegram_limit(content: Content, key: str) -> None:
    assert len(content.texts.get(key)) <= CALLBACK_ANSWER_MAX


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
