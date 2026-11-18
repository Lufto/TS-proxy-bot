"""Профиль бота в Telegram: аватар, описание и короткое описание (задача 29 bot-telegram-ui).

Ставится разово командой `scripts/set_bot_profile.py`, а не при каждом запуске бота.
Повторный запуск ничего не ломает:
- описание («Что умеет этот бот?», в пустом чате) и короткое описание («О боте», в профиле
  и в превью ссылки) меняются, только если в Telegram сейчас другой текст;
- аватар ставится, только если у бота его нет. Если аватар уже есть (поставлен раньше
  или вручную в BotFather) — не трогаем: каждая загрузка добавляет фото в историю аватаров,
  а сравнить его с нашим файлом нельзя (Telegram пережимает картинку). Заменить —
  `replace_avatar=True` (у скрипта — `--replace-avatar`).

Тексты — `content/client/bot_profile.yaml`, аватар — `content/images/avatar.jpg`
(Telegram принимает статичный аватар только в .jpg; это `avatar.png`, пересохранённый в jpg).
Картинку описания в пустом чате через API поставить нельзя — её загружает владелец
в @BotFather (шаги — `docs/areas/server.md`, «Профиль бота»).
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InputProfilePhotoStatic

from tsproxy.content.loader import Texts

AVATAR_FILE = Path("images") / "avatar.jpg"
# Чтобы узнать, есть ли у бота аватар, хватит одного фото
ONE_PHOTO = 1


class ProfilePart(StrEnum):
    DESCRIPTION = "описание"
    SHORT_DESCRIPTION = "короткое описание"
    AVATAR = "аватар"


@dataclass(frozen=True)
class BotProfile:
    description: str
    short_description: str
    avatar: Path


@dataclass(frozen=True)
class ProfileUpdate:
    """Что поменяли в Telegram. `avatar_kept` — аватар у бота уже был, его не заменяли."""

    changed: tuple[ProfilePart, ...]
    avatar_kept: bool


def bot_profile(texts: Texts, content_dir: Path) -> BotProfile:
    """Профиль из текстов и картинок папки `content/`."""
    return BotProfile(
        description=texts.get("bot_profile.description"),
        short_description=texts.get("bot_profile.short_description"),
        avatar=content_dir / AVATAR_FILE,
    )


async def apply_bot_profile(
    bot: Bot, profile: BotProfile, *, replace_avatar: bool
) -> ProfileUpdate:
    """Привести профиль бота в Telegram к `profile`, не трогая то, что уже совпадает."""
    changed: list[ProfilePart] = []
    if await _set_description(bot, profile.description):
        changed.append(ProfilePart.DESCRIPTION)
    if await _set_short_description(bot, profile.short_description):
        changed.append(ProfilePart.SHORT_DESCRIPTION)
    has_avatar = await _has_avatar(bot)
    if replace_avatar or not has_avatar:
        photo = InputProfilePhotoStatic(photo=FSInputFile(profile.avatar))
        await bot.set_my_profile_photo(photo=photo)
        changed.append(ProfilePart.AVATAR)
    return ProfileUpdate(changed=tuple(changed), avatar_kept=has_avatar and not replace_avatar)


async def _set_description(bot: Bot, text: str) -> bool:
    current = await bot.get_my_description()
    if current.description == text:
        return False
    await bot.set_my_description(description=text)
    return True


async def _set_short_description(bot: Bot, text: str) -> bool:
    current = await bot.get_my_short_description()
    if current.short_description == text:
        return False
    await bot.set_my_short_description(short_description=text)
    return True


async def _has_avatar(bot: Bot) -> bool:
    photos = await bot.get_user_profile_photos(user_id=bot.id, limit=ONE_PHOTO)
    return photos.total_count > 0
