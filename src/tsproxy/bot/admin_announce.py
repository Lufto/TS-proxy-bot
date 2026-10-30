"""`/announce` в группе администраторов (задача 35): объявление клиентам в приложении.

- `/announce <текст>` — поставить (или заменить) объявление. Обычный текст до 200 символов,
  переводы строк сохраняются, разметка Telegram (жирный, ссылки) — нет: приложение её не знает.
- `/announce off` — снять.
- `/announce` — показать, что стоит сейчас.

Как и куда ставится — `services.announcement`. Права — как у всей админки: `AdminFilter`
(администратор и группа администраторов); команду от остальных роутер молча не берёт.
"""

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.context import BotContext
from tsproxy.content.loader import Texts
from tsproxy.domain.announcement import (
    ANNOUNCE_MAX_LENGTH,
    AnnouncementProblem,
    announcement_length,
)
from tsproxy.integrations.remnawave_types import RemnawaveAuthError, RemnawaveUnavailable
from tsproxy.services.announcement import AnnouncementService

logger = logging.getLogger(__name__)

ANNOUNCE_COMMAND = "announce"
OFF_ARGUMENT = "off"


def create_admin_announce_router(access: AdminAccess, announcements: AnnouncementService) -> Router:
    router = Router(name="admin_announce")

    async def on_announce_command(
        message: Message, command: CommandObject, ctx: BotContext
    ) -> None:
        if message.from_user is None:
            return  # не бывает: `AdminFilter` пропускает только сообщения с отправителем
        texts = ctx.admin_texts
        argument = (command.args or "").strip()
        try:
            reply = await _handle(texts, announcements, message.from_user.id, argument)
        except RemnawaveUnavailable as error:
            logger.warning("/announce: панель не ответила: %s", error)
            reply = texts.get("admin.panel_unavailable")
        except RemnawaveAuthError as error:
            logger.error("/announce: панель не дала доступ к настройкам подписки: %s", error)
            reply = texts.get("admin.announce.no_rights")
        await message.answer(reply)

    router.message.register(on_announce_command, AdminFilter(access), Command(ANNOUNCE_COMMAND))
    return router


async def _handle(
    texts: Texts, announcements: AnnouncementService, admin_id: int, argument: str
) -> str:
    """Ответ администратору на `/announce` с этим аргументом."""
    if not argument:
        current = await announcements.current()
        if current is None:
            return texts.get("admin.announce.none")
        return texts.get("admin.announce.current", text=current)
    if argument.lower() == OFF_ARGUMENT:
        removed = await announcements.withdraw(admin_id)
        if removed is None:
            return texts.get("admin.announce.already_off")
        return texts.get("admin.announce.cleared", text=removed)
    problem = await announcements.publish(admin_id, argument)
    if problem is AnnouncementProblem.TOO_LONG:
        return texts.get(
            "admin.announce.too_long",
            length=announcement_length(argument),
            max_length=ANNOUNCE_MAX_LENGTH,
        )
    if problem is AnnouncementProblem.EMPTY:
        return texts.get("admin.announce.usage")
    return texts.get("admin.announce.published", text=argument)
