"""Напоминание о забытом обращении (задача 17): клиент ждёт ответа дольше часа — бот пишет
в теме обращения. Запускается фоновой задачей (`tsproxy.app`).

Одно напоминание на одно ожидание: пока клиент ждёт с того же момента, второй раз
не напоминаем. Ответили и клиент написал снова — новое ожидание, новое напоминание.
Что уже напомнили, помнит только память процесса: после перезапуска бота напоминание
по ещё не отвеченным обращениям придёт ещё раз — лишнее сообщение лучше пропущенного.
"""

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from tsproxy.bot.context import BotContext
from tsproxy.bot.support_card import SupportTopicTexts
from tsproxy.services.support import WaitingTicket

logger = logging.getLogger(__name__)


class SupportReminders:
    """Фоновая задача «напомнить о забытых обращениях»."""

    def __init__(self, bot: Bot, ctx: BotContext) -> None:
        self._bot = bot
        self._ctx = ctx
        self._reminded: set[tuple[int, datetime]] = set()  # (обращение, ждёт с)

    async def run(self) -> None:
        overdue = await self._ctx.support.overdue_tickets()
        waiting_now = {(waiting.ticket.id, waiting.since) for waiting in overdue}
        self._reminded &= waiting_now  # отвеченные и закрытые больше не помним
        for waiting in overdue:
            key = (waiting.ticket.id, waiting.since)
            if key in self._reminded:
                continue
            await self._remind(waiting)
            self._reminded.add(key)

    async def _remind(self, waiting: WaitingTicket) -> None:
        ticket = waiting.ticket
        if ticket.topic_id is None:
            return  # сервис отдаёт только обращения с темой
        screen = SupportTopicTexts(self._ctx.admin_texts).reminder(
            ticket.id, waiting.since, ticket.assignee_admin_id
        )
        try:
            await self._bot.send_message(
                self._ctx.access.admin_chat_id,
                screen.text,
                message_thread_id=ticket.topic_id,
                reply_markup=screen.markup,
            )
        except TelegramBadRequest as error:
            # тему удалили вручную: новое сообщение клиента создаст новую тему, а повторять
            # напоминание каждые несколько минут бессмысленно
            logger.warning("Обращение #%s: напоминание не отправлено: %s", ticket.id, error.message)
            return
        logger.info("Обращение #%s: напоминание об ответе", ticket.id)
