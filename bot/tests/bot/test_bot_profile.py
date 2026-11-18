"""Профиль бота (задача 29 bot-telegram-ui): аватар и оба описания — одной командой."""

from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.methods import (
    GetUserProfilePhotos,
    SetMyDescription,
    SetMyProfilePhoto,
    SetMyShortDescription,
)
from conftest import REPO_CONTENT

from tsproxy.bot.bot_profile import ProfilePart, apply_bot_profile, bot_profile
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content, ContentError

# Пределы Telegram: описание — до 512 знаков, короткое — до 120
DESCRIPTION_MAX = 512
SHORT_DESCRIPTION_MAX = 120
SITE = "ts-proxy.com"


async def test_first_run_sets_avatar_and_both_descriptions(
    bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    profile = bot_profile(content.texts, REPO_CONTENT)

    update = await apply_bot_profile(bot, profile, replace_avatar=False)

    assert set(update.changed) == set(ProfilePart)
    assert not update.avatar_kept
    assert telegram.description == profile.description
    assert telegram.short_description == profile.short_description
    assert telegram.profile_photos == 1


async def test_second_run_changes_nothing(
    bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    profile = bot_profile(content.texts, REPO_CONTENT)
    await apply_bot_profile(bot, profile, replace_avatar=False)
    telegram.requests.clear()

    update = await apply_bot_profile(bot, profile, replace_avatar=False)

    assert update.changed == ()
    assert update.avatar_kept  # аватар уже стоит — второй такой же в историю не грузим
    assert telegram.sent(SetMyDescription) == []
    assert telegram.sent(SetMyShortDescription) == []
    assert telegram.sent(SetMyProfilePhoto) == []
    assert telegram.profile_photos == 1


async def test_existing_avatar_is_kept_unless_asked_to_replace(
    bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    telegram.profile_photos = 1  # аватар поставили раньше вручную в BotFather
    profile = bot_profile(content.texts, REPO_CONTENT)

    kept = await apply_bot_profile(bot, profile, replace_avatar=False)
    assert ProfilePart.AVATAR not in kept.changed
    assert kept.avatar_kept

    replaced = await apply_bot_profile(bot, profile, replace_avatar=True)
    assert replaced.changed == (ProfilePart.AVATAR,)
    assert telegram.profile_photos == 2


async def test_changed_text_is_updated(
    bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    telegram.description = "Старое описание"
    telegram.short_description = "Старое"
    telegram.profile_photos = 1
    profile = bot_profile(content.texts, REPO_CONTENT)

    update = await apply_bot_profile(bot, profile, replace_avatar=False)

    assert set(update.changed) == {ProfilePart.DESCRIPTION, ProfilePart.SHORT_DESCRIPTION}
    assert telegram.description == profile.description


def test_profile_texts_fit_telegram_limits(content: Content) -> None:
    profile = bot_profile(content.texts, REPO_CONTENT)
    assert len(profile.description) <= DESCRIPTION_MAX
    assert len(profile.short_description) <= SHORT_DESCRIPTION_MAX
    assert SITE in profile.description
    assert SITE in profile.short_description
    assert "<" not in profile.description + profile.short_description  # Telegram не разбирает HTML


async def test_replace_avatar_does_not_ask_for_current_avatar(
    bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    """Заменяем в любом случае — спрашивать Telegram, есть ли аватар, незачем."""
    profile = bot_profile(content.texts, REPO_CONTENT)

    update = await apply_bot_profile(bot, profile, replace_avatar=True)

    assert telegram.sent(GetUserProfilePhotos) == []
    assert ProfilePart.AVATAR in update.changed
    assert not update.avatar_kept


async def test_fake_telegram_knows_only_bot_avatars(bot: Bot) -> None:
    """Подделка помнит только аватары бота: фото клиентов она не знает — ошибка,
    а не «фото нет», чтобы тест не прошёл на выдуманном ответе."""
    with pytest.raises(NotImplementedError, match="фото профиля"):
        await bot.get_user_profile_photos(user_id=bot.id + 1)


def test_missing_avatar_is_clear_error_before_telegram(
    tmp_path: Path, telegram: FakeTelegramSession, content: Content
) -> None:
    """Нет `avatar.jpg` — понятная ошибка ещё до Telegram: профиль не ставится наполовину."""
    with pytest.raises(ContentError, match=r"avatar\.jpg"):
        bot_profile(content.texts, tmp_path)

    assert telegram.requests == []


def test_avatar_is_jpeg(content: Content) -> None:
    """`setMyProfilePhoto` принимает статичный аватар только в .jpg."""
    avatar: Path = bot_profile(content.texts, REPO_CONTENT).avatar
    assert avatar.read_bytes()[:3] == b"\xff\xd8\xff"
