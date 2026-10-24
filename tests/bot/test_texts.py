"""Тексты экранов: все на месте, без слова «VPN», влезают в ограничения Telegram."""

import re
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import REPO_CONTENT

from tsproxy.bot.callbacks import InstructionsCallback, MenuCallback, Platform, ScreenId
from tsproxy.bot.screens import PLACEHOLDER_TEXTS, Screens
from tsproxy.content.loader import (
    ADMIN_TEXTS_FILE,
    REQUIRED_TEXTS,
    TEXTS_FILE,
    Content,
    ContentError,
    Texts,
    load_content,
)
from tsproxy.integrations.fakes import FakeRemnawave

# Telegram: текст всплывающего ответа на кнопку — до 200 символов, данные кнопки — до 64 байт
CALLBACK_ANSWER_MAX = 200
CALLBACK_DATA_MAX_BYTES = 64
FORBIDDEN_WORDS = re.compile(r"vpn|впн", re.IGNORECASE)


def test_missing_required_text_stops_startup(tmp_path: Path) -> None:
    shutil.copytree(REPO_CONTENT, tmp_path, dirs_exist_ok=True)
    texts_path = tmp_path / TEXTS_FILE
    data = yaml.safe_load(texts_path.read_text(encoding="utf-8"))
    del data["connect"]["no_account_trial"]
    texts_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ContentError, match=r"connect\.no_account_trial"):
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
    screens.instructions_menu()
    for platform in Platform:
        screens.instructions(platform)
    for screen in PLACEHOLDER_TEXTS:
        screens.placeholder(screen)
    screens.connect(account)
    screens.no_account(trial_available=True)
    screens.no_account(trial_available=False)
    screens.unavailable()
    screens.error()
    screens.unknown_command()


# Тексты с подстановками; у остальных обязательных текстов подстановок нет
TEXT_PLACEHOLDERS: dict[str, set[str]] = {
    "connect.active": {"expire_at"},
    "connect.inactive": {"expire_at"},
    "support.ticket_created": {"ticket_id"},
}


def test_texts_placeholders_are_expected(content: Content) -> None:
    for key in REQUIRED_TEXTS:
        expected = TEXT_PLACEHOLDERS.get(key, set())
        assert content.texts.placeholders(key) == expected, key


@pytest.mark.parametrize("key", ["throttle.callback", "errors.stale_button"])
def test_callback_answers_fit_telegram_limit(content: Content, key: str) -> None:
    assert len(content.texts.get(key)) <= CALLBACK_ANSWER_MAX


def test_callback_data_fits_telegram_limit() -> None:
    packed = [MenuCallback(screen=s).pack() for s in ScreenId]
    packed += [InstructionsCallback(platform=p).pack() for p in Platform]
    assert all(len(data.encode()) <= CALLBACK_DATA_MAX_BYTES for data in packed)
