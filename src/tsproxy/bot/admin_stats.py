"""`/stats` в группе администраторов (задача 19): статистика одним сообщением.

Что и откуда считается — `services.stats`. Права — как у всей админки: `AdminFilter`
(администратор и группа администраторов); команду от остальных роутер молча не берёт.
"""

from datetime import timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.context import BotContext
from tsproxy.content.loader import Texts
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.domain.stats import split_minutes
from tsproxy.services.stats import SalesTotal, Stats, StatsService

STATS_COMMAND = "stats"
LINE = "\n"


def format_duration(texts: Texts, duration: timedelta) -> str:
    """«12 мин», «1 ч 05 мин» или «меньше минуты»."""
    hours, minutes = split_minutes(duration)
    if hours:
        return texts.get("admin.stats.duration_hours", hours=hours, minutes=f"{minutes:02d}")
    if minutes:
        return texts.get("admin.stats.duration_minutes", minutes=minutes)
    return texts.get("admin.stats.duration_under_minute")


def _sales(texts: Texts, key: str, sales: SalesTotal) -> str:
    return texts.get(key, count=sales.count, amount=format_rubles(sales.amount))


def _stars(texts: Texts, key: str, sales: SalesTotal) -> str:
    """Оплаты звёздами Telegram отдельной строкой (задача 33)."""
    stars = sales.stars
    return texts.get(
        key,
        count=stars.count,
        stars=stars.stars,
        rubles=format_rubles(stars.rubles),
        refunds=stars.refunds,
    )


def stats_text(texts: Texts, stats: Stats) -> str:
    """Статистика одним сообщением (HTML)."""
    lines = [
        texts.get("admin.stats.title", at=format_moscow(stats.collected_at)),
        _sales(texts, "admin.stats.sales_today", stats.sales_today),
        _sales(texts, "admin.stats.sales_month", stats.sales_month),
        texts.get(
            "admin.stats.refunds",
            today_count=stats.sales_today.refunds.count,
            today_amount=format_rubles(stats.sales_today.refunds.amount),
            month_count=stats.sales_month.refunds.count,
            month_amount=format_rubles(stats.sales_month.refunds.amount),
        ),
        _stars(texts, "admin.stats.stars_today", stats.sales_today),
        _stars(texts, "admin.stats.stars_month", stats.sales_month),
    ]
    subscriptions = stats.subscriptions
    if subscriptions is None:
        lines.append(texts.get("admin.stats.subscriptions_unknown"))
    else:
        lines.append(
            texts.get(
                "admin.stats.subscriptions",
                paid=subscriptions.paid,
                grace=subscriptions.grace,
                trial=subscriptions.trial,
            )
        )
    trials = stats.trials
    if trials.percent is None:
        lines.append(texts.get("admin.stats.trials_none"))
    else:
        lines.append(
            texts.get(
                "admin.stats.trials",
                converted=trials.converted,
                trials=trials.trials,
                percent=trials.percent,
            )
        )
    support = stats.support
    lines.append(
        texts.get(
            "admin.stats.support",
            open_now=support.open_now,
            waiting=support.waiting_first_reply,
            opened=support.opened_month,
        )
    )
    if support.response is None:
        lines.append(texts.get("admin.stats.response_none"))
    else:
        lines.append(
            texts.get(
                "admin.stats.response",
                average=format_duration(texts, support.response.average),
                longest=format_duration(texts, support.response.longest),
            )
        )
    lines.append(
        texts.get("admin.stats.clients", total=stats.clients.total, blocked=stats.clients.blocked)
    )
    return LINE.join(lines)


def create_admin_stats_router(access: AdminAccess, stats: StatsService) -> Router:
    router = Router(name="admin_stats")

    async def on_stats_command(message: Message, ctx: BotContext) -> None:
        await message.answer(stats_text(ctx.admin_texts, await stats.collect()))

    router.message.register(on_stats_command, AdminFilter(access), Command(STATS_COMMAND))
    return router
