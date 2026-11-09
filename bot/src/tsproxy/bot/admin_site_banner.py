"""`/banner` в группе администраторов: плашка о неполадках на сайте (задачи сайта 14, 15).

Экран показывает, какая плашка стоит, и кнопками ставит другую или снимает. Та же кнопка
«Поставить плашку „перебои у прокси“» приходит под тревогой «Remnawave не отвечает»
(`banner_alert_button`). Действие обратимо одной кнопкой — без подтверждения.

Права — как у всей админки: `AdminFilter` на каждом обработчике (администратор и группа
администраторов). Команду от остальных роутер молча не берёт; кнопку — всплывающий отказ.
"""

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_clients import on_foreign_press
from tsproxy.bot.admin_screens import AdminScreen
from tsproxy.bot.context import BotContext
from tsproxy.content.loader import Texts
from tsproxy.domain.site_banner import SiteBanner
from tsproxy.services.alerts import AlertButton

BANNER_COMMAND = "banner"
PARAGRAPH = "\n\n"


class AdminSiteBannerCallback(CallbackData, prefix="admsb"):
    """Какую плашку поставить (`none` — снять). `edit=False` — нажали под тревогой:
    тревогу не трогаем, экран присылаем ответом на неё."""

    banner: SiteBanner
    edit: bool = True


def create_admin_site_banner_router(access: AdminAccess) -> Router:
    router = Router(name="admin_site_banner")
    admin = AdminFilter(access)
    router.message.register(on_banner_command, admin, Command(BANNER_COMMAND))
    router.callback_query.register(on_banner_set, admin, AdminSiteBannerCallback.filter())
    # Сюда доходят только нажатия, которые не прошли `AdminFilter`
    router.callback_query.register(on_foreign_press, AdminSiteBannerCallback.filter())
    return router


def banner_alert_button(texts: Texts) -> AlertButton:
    """Кнопка под тревогой «Remnawave не отвечает» (задача сайта 15)."""
    return AlertButton(
        text=texts.get("admin.site_banner.alert_button"),
        callback_data=AdminSiteBannerCallback(banner=SiteBanner.PROXY_ISSUES, edit=False).pack(),
    )


def banner_screen(
    texts: Texts, current: SiteBanner, *, notice_key: str | None = None
) -> AdminScreen:
    """Какая плашка стоит и кнопки для остальных состояний. `notice_key` — итог нажатия сверху."""
    state = texts.get(
        "admin.site_banner.state", banner=texts.get(f"admin.site_banner.names.{current.value}")
    )
    buttons = [
        [
            InlineKeyboardButton(
                text=texts.get(f"admin.site_banner.buttons.{banner.value}"),
                callback_data=AdminSiteBannerCallback(banner=banner).pack(),
            )
        ]
        for banner in SiteBanner
        if banner is not current
    ]
    notice = [] if notice_key is None else [texts.get(notice_key)]
    return AdminScreen(
        PARAGRAPH.join([*notice, state]), InlineKeyboardMarkup(inline_keyboard=buttons)
    )


async def on_banner_command(message: Message, ctx: BotContext) -> None:
    screen = banner_screen(ctx.admin_texts, await ctx.site_banner.current())
    await message.answer(screen.text, reply_markup=screen.markup)


async def on_banner_set(
    callback: CallbackQuery, callback_data: AdminSiteBannerCallback, bot: Bot, ctx: BotContext
) -> None:
    await callback.answer()
    changed = await ctx.site_banner.set(callback_data.banner, callback.from_user.id)
    screen = banner_screen(
        ctx.admin_texts,
        await ctx.site_banner.current(),
        notice_key="admin.site_banner.switched" if changed else "admin.site_banner.unchanged",
    )
    message = callback.message if isinstance(callback.message, Message) else None
    if message is not None and callback_data.edit:
        await message.edit_text(screen.text, reply_markup=screen.markup)
        return
    if message is not None:
        await message.reply(screen.text, reply_markup=screen.markup)
        return
    # Сообщение слишком старое (Telegram его не отдаёт) — пишем в группу без темы
    await bot.send_message(ctx.access.admin_chat_id, screen.text, reply_markup=screen.markup)
