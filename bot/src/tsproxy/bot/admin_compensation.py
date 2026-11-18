"""Компенсация при сбое ноды в Telegram (задача 20): кнопка в «🚨 Тревоги», ход и итог,
извинение клиентам.

Нода восстановилась → сообщение с длительностью простоя и «🎁 Компенсировать N» →
подтверждение «✅ Да, компенсировать» → начисление в фоне, ход и итог — новыми сообщениями
в той же теме. Под предложением остаётся «🔁 Продолжить»: если бот перезапускался посреди
начисления, нажатие доделает его, никому не начислив дважды (`CompensationService`).

`/outages` — последние сбои с теми же кнопками: если предложение не дошло до Telegram или
потерялось в теме, кнопку можно получить командой.

Права — как у всей админки: `AdminFilter` на обработчике; данные кнопки прав не дают.
"""

import logging
from datetime import timedelta
from enum import StrEnum
from typing import Annotated

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from pydantic import Field

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_clients import on_foreign_press
from tsproxy.bot.admin_screens import AdminScreen
from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.delivery import ClientDelivery
from tsproxy.bot.screens import Screen
from tsproxy.content.dates import client_time
from tsproxy.content.loader import Texts
from tsproxy.domain.compensation import CompensationAction, Outage
from tsproxy.domain.constants import ONE_DAY, ONE_HOUR
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.profile import plural_form
from tsproxy.services.compensation import (
    RECENT_OUTAGES,
    Apology,
    ClientOutcome,
    CompensationOffer,
    CompensationReport,
    CompensationRun,
    CompensationService,
    OutageSummary,
    StartOutcome,
)
from tsproxy.services.nodes import outage_minutes

logger = logging.getLogger(__name__)

OUTAGES_COMMAND = "outages"
LINE = "\n"
PARAGRAPH = "\n\n"
TELEGRAM_IDS_SEPARATOR = ", "


class CompensationStep(StrEnum):
    ASK = "ask"  # «🎁 Компенсировать N» — показать подтверждение
    CONFIRM = "confirm"  # «✅ Да, компенсировать»
    CANCEL = "cancel"  # «✖️ Отмена» — вернуть предложение
    RESUME = "resume"  # «🔁 Продолжить» — доделать начатое


class CompensationCallback(CallbackData, prefix="admcmp"):
    """Кнопки компенсации: несут только id сбоя и шаг."""

    outage_id: Annotated[int, Field(gt=0)]
    step: CompensationStep


START_POPUPS: dict[StartOutcome, str] = {
    StartOutcome.STARTED: "admin.compensation.launched",
    StartOutcome.RESUMED: "admin.compensation.resumed",
    StartOutcome.NOT_FOUND: "admin.compensation.not_found",
    StartOutcome.STILL_OPEN: "admin.compensation.still_open",
}


def duration_words(units: Texts, amount: timedelta) -> str:
    """«1 день 3 часа», «2 дня», «5 часов» — по словам из `units.*` в `content/client/`."""
    days, rest = divmod(amount, ONE_DAY)
    hours = rest // ONE_HOUR
    parts = [
        units.get(f"units.{unit}_{plural_form(number).value}", n=number)
        for unit, number in (("days", days), ("hours", hours))
        if number
    ]
    return " ".join(parts) or units.get("units.less_than_hour")


class CompensationScreens:
    """Сообщения о компенсации в группе администраторов."""

    def __init__(self, admin_texts: Texts, units: Texts) -> None:
        self._texts = admin_texts
        self._units = units

    def amount(self, amount: timedelta) -> str:
        return duration_words(self._units, amount)

    def _button(
        self, key: str, outage_id: int, step: CompensationStep, **values: object
    ) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self._texts.get(key, **values),
            callback_data=CompensationCallback(outage_id=outage_id, step=step).pack(),
        )

    def _incident(self, incident: Outage) -> dict[str, object]:
        return {
            "since": format_moscow(incident.started_at),
            "until": format_moscow(incident.ended_at),
            "minutes": outage_minutes(incident.started_at, incident.ended_at),
        }

    def offer(self, offer: CompensationOffer) -> AdminScreen:
        amount = self.amount(offer.amount)
        text = self._texts.get(
            "admin.compensation.offer", amount=amount, **self._incident(offer.incident)
        )
        button = self._button(
            "admin.compensation.button", offer.outage_id, CompensationStep.ASK, amount=amount
        )
        return AdminScreen(text, InlineKeyboardMarkup(inline_keyboard=[[button]]))

    def confirm(self, offer: CompensationOffer) -> AdminScreen:
        amount = self.amount(offer.amount)
        text = PARAGRAPH.join(
            [
                self._texts.get(
                    "admin.compensation.offer", amount=amount, **self._incident(offer.incident)
                ),
                self._texts.get("admin.compensation.confirm", amount=amount),
            ]
        )
        buttons = [
            self._button(
                "admin.compensation.confirm_button", offer.outage_id, CompensationStep.CONFIRM
            ),
            self._button("admin.buttons.cancel", offer.outage_id, CompensationStep.CANCEL),
        ]
        return AdminScreen(text, InlineKeyboardMarkup(inline_keyboard=[buttons]))

    def in_progress(self, run: CompensationRun) -> AdminScreen:
        """Предложение после нажатия: что начисляется и «🔁 Продолжить» на случай перезапуска."""
        text = self._texts.get(
            "admin.compensation.in_progress",
            run_id=run.run_id,
            amount=self.amount(run.amount),
            **self._incident(run.incident),
        )
        return AdminScreen(text, self._resume_markup(run.outage_id))

    def _resume_markup(self, outage_id: int) -> InlineKeyboardMarkup:
        button = self._button(
            "admin.compensation.resume_button", outage_id, CompensationStep.RESUME
        )
        return InlineKeyboardMarkup(inline_keyboard=[[button]])

    def started(self, run: CompensationRun, total: int) -> str:
        return self._texts.get(
            "admin.compensation.started",
            run_id=run.run_id,
            amount=self.amount(run.amount),
            total=total,
        )

    def progress(self, run: CompensationRun, done: int, total: int) -> str:
        return self._texts.get(
            "admin.compensation.progress", run_id=run.run_id, done=done, total=total
        )

    def finished(self, report: CompensationReport) -> AdminScreen:
        run = report.run
        if report.total == 0 and run.amount <= timedelta(0):
            return AdminScreen(self._texts.get("admin.compensation.nothing", run_id=run.run_id))
        counts = report.counts
        parts = [
            self._texts.get(
                "admin.compensation.finished",
                run_id=run.run_id,
                amount=self.amount(run.amount),
                extended=counts[ClientOutcome.EXTENDED],
                grace_extended=counts[ClientOutcome.GRACE_EXTENDED],
                already=counts[ClientOutcome.ALREADY_COMPENSATED],
                not_eligible=counts[ClientOutcome.NOT_ELIGIBLE],
                skipped=counts[ClientOutcome.SKIPPED],
            )
        ]
        if report.unfinished:
            parts.append(
                self._texts.get(
                    "admin.compensation.unfinished",
                    deferred=counts[ClientOutcome.DEFERRED],
                    failed=counts[ClientOutcome.FAILED],
                )
            )
        if report.manual:
            ids = TELEGRAM_IDS_SEPARATOR.join(str(telegram_id) for telegram_id in report.manual)
            parts.append(self._texts.get("admin.compensation.manual", telegram_ids=ids))
        markup = self._resume_markup(run.outage_id) if report.unfinished else None
        return AdminScreen(LINE.join(parts), markup)

    def outages(self, outages: list[OutageSummary], limit: int) -> AdminScreen:
        """`/outages`: сбои по строке и по кнопке на каждый, который можно компенсировать
        («🎁») или доделать («🔁»)."""
        if not outages:
            return AdminScreen(self._texts.get("admin.outages.empty"))
        lines = [self._texts.get("admin.outages.title", limit=limit)]
        lines += [self._outage_line(outage) for outage in outages]
        rows = [[button] for outage in outages if (button := self._outage_button(outage))]
        if rows:
            lines.append(self._texts.get("admin.outages.footer"))
        markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
        return AdminScreen(PARAGRAPH.join(lines), markup)

    def _outage_line(self, outage: OutageSummary) -> str:
        node = outage.node_name or outage.node_uuid
        since = format_moscow(outage.started_at)
        if outage.ended_at is None:
            return self._texts.get(
                "admin.outages.open", outage_id=outage.outage_id, node=node, since=since
            )
        values: dict[str, object] = {
            "outage_id": outage.outage_id,
            "node": node,
            "since": since,
            "until": format_moscow(outage.ended_at),
            "minutes": outage_minutes(outage.started_at, outage.ended_at),
        }
        if outage.compensated_at is not None:
            return self._texts.get(
                "admin.outages.compensated",
                at=format_moscow(outage.compensated_at),
                admin_id=outage.compensated_by_admin_id or self._texts.get("admin.no_value"),
                **values,
            )
        if outage.amount > timedelta(0):
            return self._texts.get(
                "admin.outages.waiting", amount=self.amount(outage.amount), **values
            )
        return self._texts.get("admin.outages.covered", **values)

    def _outage_button(self, outage: OutageSummary) -> InlineKeyboardButton | None:
        if outage.compensated_at is not None:
            return self._button(
                "admin.outages.resume_button",
                outage.outage_id,
                CompensationStep.RESUME,
                number=outage.outage_id,
            )
        if outage.ended_at is not None and outage.amount > timedelta(0):
            return self._button(
                "admin.outages.ask_button",
                outage.outage_id,
                CompensationStep.ASK,
                number=outage.outage_id,
                amount=self.amount(outage.amount),
            )
        return None  # нода ещё лежит или сбой уже покрыт соседней компенсацией


class TelegramCompensationReporter:
    """Предложение, ход и итог компенсации в «🚨 Тревоги». Реализует `CompensationReporter`.

    Ход — правкой одного сообщения, итог — новым (чтобы его было видно в теме).
    Ошибки Telegram — в лог: начисление важнее отчёта о нём.
    """

    def __init__(
        self, bot: Bot, screens: CompensationScreens, chat_id: int, thread_id: int | None
    ) -> None:
        self._bot = bot
        self._screens = screens
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._progress_messages: dict[int, int] = {}  # прогон → сообщение с ходом

    async def offer(self, offer: CompensationOffer) -> None:
        await self._send(self._screens.offer(offer))

    async def started(self, run: CompensationRun, total: int) -> None:
        message = await self._send(AdminScreen(self._screens.started(run, total)))
        if message is not None:
            self._progress_messages[run.run_id] = message.message_id

    async def progress(self, run: CompensationRun, done: int, total: int) -> None:
        message_id = self._progress_messages.get(run.run_id)
        text = self._screens.progress(run, done, total)
        if message_id is None:
            await self._send(AdminScreen(text))
            return
        try:
            await self._bot.edit_message_text(text, chat_id=self._chat_id, message_id=message_id)
        except TelegramAPIError:
            logger.exception("Компенсация #%s: ход не обновился", run.run_id)

    async def finished(self, report: CompensationReport) -> None:
        self._progress_messages.pop(report.run.run_id, None)
        await self._send(self._screens.finished(report))

    async def _send(self, screen: AdminScreen) -> Message | None:
        try:
            return await self._bot.send_message(
                self._chat_id,
                screen.text,
                message_thread_id=self._thread_id,
                reply_markup=screen.markup,
            )
        except TelegramAPIError:
            logger.exception("Сообщение о компенсации не ушло в группу администраторов")
            return None


class TelegramApologyMessenger:
    """Извинение клиенту после сбоя. Реализует `ApologyMessenger`."""

    def __init__(self, delivery: ClientDelivery, texts: Texts) -> None:
        self._delivery = delivery
        self._texts = texts

    async def send_apology(self, telegram_id: int, apology: Apology) -> bool:
        return await self._delivery.send(telegram_id, self.screen(apology).kept()) is not None

    def screen(self, apology: Apology) -> Screen:
        values = {
            "amount": duration_words(self._texts, apology.amount),
            "until": client_time(apology.until),
        }
        if apology.action is CompensationAction.EXTEND_GRACE:
            # в льготном подписка кончилась — зовём продлить
            text = self._texts.get("compensation.grace_extended", **values)
            button = self._menu_button("buttons.renew", ScreenId.TARIFFS)
        else:
            text = self._texts.get("compensation.extended", **values)
            button = self._menu_button("buttons.profile", ScreenId.PROFILE)
        return Screen(text, InlineKeyboardMarkup(inline_keyboard=[[button]]))

    def _menu_button(self, key: str, screen: ScreenId) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=self._texts.get(key), callback_data=MenuCallback(screen=screen).pack()
        )


class CompensationButtons:
    """Обработчик кнопок компенсации и `/outages` (только администраторы в группе
    администраторов)."""

    def __init__(
        self, service: CompensationService, screens: CompensationScreens, admin_texts: Texts
    ) -> None:
        self._service = service
        self._screens = screens
        self._texts = admin_texts

    async def on_press(self, callback: CallbackQuery, callback_data: CompensationCallback) -> None:
        outage_id = callback_data.outage_id
        if callback_data.step is CompensationStep.CONFIRM:
            await self._start(callback, outage_id)
            return
        offer = await self._service.describe(outage_id)
        if offer is None:
            await callback.answer(self._texts.get("admin.compensation.not_found"), show_alert=True)
            return
        if callback_data.step is CompensationStep.CANCEL:
            await callback.answer()
            if offer.amount > timedelta(0):
                await _edit(callback, self._screens.offer(offer))
            return  # иначе другой администратор уже компенсировал — «Отмена» ничего не запускает
        if offer.amount <= timedelta(0):
            # всё по этому сбою уже отмечено: «Продолжить» доделывает начатое без вопросов
            await self._start(callback, outage_id)
            return
        screen = self._screens.confirm(offer)  # новое начисление — только с подтверждением
        await callback.answer()
        await _edit(callback, screen)

    async def on_outages(self, message: Message) -> None:
        """`/outages` — последние сбои с кнопками."""
        outages = await self._service.recent_outages()
        screen = self._screens.outages(outages, RECENT_OUTAGES)
        await message.answer(screen.text, reply_markup=screen.markup)

    async def _start(self, callback: CallbackQuery, outage_id: int) -> None:
        started = await self._service.compensate(outage_id, callback.from_user.id)
        if started.run is None:
            await callback.answer(self._texts.get(START_POPUPS[started.outcome]), show_alert=True)
            return
        if self._service.launch(started.run) is None:
            await callback.answer(self._texts.get("admin.compensation.running"), show_alert=True)
            return
        await callback.answer(self._texts.get(START_POPUPS[started.outcome]))
        await _edit(callback, self._screens.in_progress(started.run))


async def _edit(callback: CallbackQuery, screen: AdminScreen) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(screen.text, reply_markup=screen.markup)


def create_admin_compensation_router(access: AdminAccess, buttons: CompensationButtons) -> Router:
    router = Router(name="admin_compensation")
    router.message.register(buttons.on_outages, AdminFilter(access), Command(OUTAGES_COMMAND))
    router.callback_query.register(
        buttons.on_press, AdminFilter(access), CompensationCallback.filter()
    )
    # Сюда доходят только нажатия, которые не прошли `AdminFilter`
    router.callback_query.register(on_foreign_press, CompensationCallback.filter())
    return router
