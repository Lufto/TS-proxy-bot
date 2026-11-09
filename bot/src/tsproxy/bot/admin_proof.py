"""`/proof 123` в группе администраторов (задача 22): доказательство оказания услуги
по заказу — текстовым файлом, который можно приложить к спору с платёжкой.

Что собирается — `services.service_proof`. Права — как у всей админки (`AdminFilter`).
Файл обычный текст (`.txt`): тексты из `content/admin/` подставляют значения
с HTML-экранированием, поэтому перед записью в файл экранирование снимается.
"""

import html
import json
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.context import BotContext
from tsproxy.content.loader import Texts
from tsproxy.domain.constants import BYTES_PER_GB
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.money import format_rubles
from tsproxy.services.service_proof import PanelFacts, ProofEvent, ServiceProof, ServiceProofService

PROOF_COMMAND = "proof"
PROOF_FILE_NAME = "proof-order-{order_id}.txt"
LINE = "\n"


def create_admin_proof_router(access: AdminAccess, proofs: ServiceProofService) -> Router:
    router = Router(name="admin_proof")

    async def on_proof_command(message: Message, command: CommandObject, ctx: BotContext) -> None:
        texts = ctx.admin_texts
        raw = (command.args or "").strip().removeprefix("#")
        if not raw.isdigit():
            await message.answer(texts.get("admin.proof.usage"))
            return
        order_id = int(raw)
        proof = await proofs.collect(order_id)
        if proof is None:
            await message.answer(texts.get("admin.proof.not_found", order_id=order_id))
            return
        document = BufferedInputFile(
            proof_text(texts, proof).encode("utf-8"),
            filename=PROOF_FILE_NAME.format(order_id=order_id),
        )
        await message.answer_document(
            document, caption=texts.get("admin.proof.caption", order_id=order_id)
        )

    router.message.register(on_proof_command, AdminFilter(access), Command(PROOF_COMMAND))
    return router


def proof_text(texts: Texts, proof: ServiceProof) -> str:
    """Доказательство обычным текстом."""
    order, client = proof.order, proof.client
    no_value = texts.get("admin.no_value")
    lines = [
        texts.get("admin.proof.header", order_id=order.id, at=format_moscow(proof.collected_at)),
        texts.get("admin.proof.service", tariff_id=order.tariff_id, days=order.days),
        texts.get(
            "admin.proof.client",
            telegram_id=client.telegram_id,
            name=client.name or no_value,
            username=client.username or no_value,
            first_seen_at=format_moscow(client.first_seen_at),
        ),
        texts.get(
            "admin.proof.order",
            created_at=format_moscow(order.created_at),
            price=format_rubles(order.price),
            status=texts.get(f"admin.order_status.{order.status.value}"),
        ),
    ]
    if order.paid_at is None:
        lines.append(texts.get("admin.proof.payment_none"))
    else:
        lines.append(
            texts.get(
                "admin.proof.payment",
                paid_at=format_moscow(order.paid_at),
                amount=format_rubles(order.paid_amount) if order.paid_amount else no_value,
                provider=order.payment_provider or no_value,
                payment_id=order.payment_id or no_value,
            )
        )
    if order.grant_target_expire_at is not None:
        lines.append(
            texts.get(
                "admin.proof.grant_target", expire_at=format_moscow(order.grant_target_expire_at)
            )
        )
    lines += _panel_lines(texts, proof.panel)
    lines.append(texts.get("admin.proof.events_title"))
    lines += [_event_line(texts, event) for event in proof.events]
    if proof.truncated:
        lines.append(texts.get("admin.proof.truncated", limit=len(proof.events)))
    # значения экранированы для HTML (Texts.get), а в файле нужен обычный текст
    return html.unescape(LINE.join(lines)) + LINE


def _panel_lines(texts: Texts, panel: PanelFacts) -> list[str]:
    if not panel.available and panel.account is None:
        return [texts.get("admin.proof.panel_unavailable")]
    account = panel.account
    if account is None:
        return [texts.get("admin.proof.account_none")]
    first = account.first_connected_at
    lines = [
        texts.get(
            "admin.proof.account",
            status=account.status.value,
            expire_at=format_moscow(account.expire_at),
            first_connected=(
                format_moscow(first) if first else texts.get("admin.proof.never_connected")
            ),
            traffic=f"{account.traffic_used_bytes / BYTES_PER_GB:.2f}",
        )
    ]
    if not panel.available:
        lines.append(texts.get("admin.proof.panel_unavailable"))
        return lines
    lines.append(texts.get("admin.proof.devices_title", count=len(panel.devices)))
    unknown = texts.get("admin.devices.unknown")
    lines += [
        texts.get(
            "admin.proof.device",
            model=device.device_model or unknown,
            platform=" ".join(p for p in (device.platform, device.os_version) if p) or unknown,
            connected_at=format_moscow(device.created_at),
        )
        for device in panel.devices
    ]
    return lines


def _event_line(texts: Texts, event: ProofEvent) -> str:
    values = {
        "at": format_moscow(event.occurred_at),
        "kind": texts.get(f"admin.proof.kinds.{event.kind}"),
        "details": _details(event.details),
    }
    key = "admin.proof.event" if event.admin_id is None else "admin.proof.event_admin"
    if event.admin_id is not None:
        values["admin_id"] = event.admin_id
    line = texts.get(key, **values)
    if event.repeat_count > 1:  # склеенные повторы (например, поддельные уведомления)
        line += LINE + texts.get("admin.proof.repeated", count=event.repeat_count)
    return line


def _details(details: dict[str, object] | None) -> str:
    """Подробности события одной строкой; даты и прочее — как записаны в журнале."""
    if not details:
        return "—"
    return json.dumps(details, ensure_ascii=False, sort_keys=True, default=_plain)


def _plain(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
