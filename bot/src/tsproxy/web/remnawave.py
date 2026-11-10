"""Приём вебхуков панели Remnawave 3.4.5 (задача 09, аудит §4.2).

Формат — по исходникам панели, тег 3.4.5 (github.com/remnawave/backend):
- `src/integration-modules/notifications/webhook-module/events/webhook.events.ts` —
  тело `{"scope", "event", "timestamp", "data", "meta"}`; в `data` у событий `user.*` —
  аккаунт целиком (`username` и др.), у `user_hwid_devices.*` — `{"user", "hwidUserDevice"}`,
  у `node.*` — нода;
- `src/queue/notifications/webhook-logger/webhook-logger.processor.ts` — заголовок
  `X-Remnawave-Signature`: HMAC-SHA256 (hex) от тела запроса как есть, ключ —
  `WEBHOOK_SECRET_HEADER` из `.env` панели. Так же проверяет и SDK `remnawave-api` 3.4.5
  (`remnawave.controllers.webhooks.WebhookUtility`).

Порядок проверки:
1. Тело больше 64 КБ — 413 (аудит §4.5), подпись не считаем.
2. Подпись считаем от **сырых байтов** тела, до разбора JSON, и сравниваем за постоянное
   время. Неверная — 403, тревога администраторам (склеиваются): это подделка или разные
   секреты у панели и бота, тогда все вебхуки теряются и работает только сверка.
3. Подпись верна — всегда 200, даже если событие не разобрали, устарело или панель не
   ответила на перечитывание (аудит §6.1: иначе панель долбит повторами). Всё такое
   пишется в лог, а пропущенное догонит сверка.

Вебхук — только сигнал: дальше передаётся лишь имя события и имя аккаунта, состояние
сервисы перечитывают из API. Поэтому повтор старого подписанного тела ничего не ломает;
события старше 10 минут всё равно отбрасываем.
"""

import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Request, Response, status
from pydantic import AwareDatetime, BaseModel

from tsproxy.integrations.remnawave_types import RemnawaveError
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.remnawave_signals import (
    ACCOUNT_EVENT_PREFIXES,
    RemnawaveSignal,
    RemnawaveSignals,
)

logger = logging.getLogger(__name__)

REMNAWAVE_WEBHOOK_PATH = "/remnawave/webhook"
SIGNATURE_HEADER = "X-Remnawave-Signature"
MAX_BODY_BYTES = 64 * 1024
MAX_EVENT_AGE = timedelta(minutes=10)
HWID_EVENT_PREFIX = "user_hwid_devices."
FORGED_WEBHOOK_ALERT = "remnawave_webhook_forged"


def signature_valid(body: bytes, received: str | None, secret: str) -> bool:
    """Подпись панели: HMAC-SHA256 (hex) от сырого тела. Сравнение за постоянное время."""
    if not received:
        return False
    expected = hmac.new(secret.encode(), body, sha256).hexdigest()
    # байты, а не строки: строка не из ASCII в compare_digest бросила бы TypeError
    return hmac.compare_digest(expected.encode(), received.encode())


class _AccountData(BaseModel):
    """Из аккаунта в вебхуке берём только имя — остальное перечитаем из API."""

    username: str


class _HwidEventData(BaseModel):
    user: _AccountData


class WebhookBody(BaseModel):
    """Тело вебхука Remnawave. Лишние поля (`scope`, `meta`, …) не нужны и не проверяются."""

    event: str
    timestamp: AwareDatetime
    data: Any = None


def to_signal(body: WebhookBody) -> RemnawaveSignal:
    """Сигнал из тела вебхука. ValidationError — событие об аккаунте без имени аккаунта."""
    if body.event.startswith(HWID_EVENT_PREFIX):
        username = _HwidEventData.model_validate(body.data).user.username
    elif body.event.startswith(ACCOUNT_EVENT_PREFIXES):
        username = _AccountData.model_validate(body.data).username
    else:
        username = None
    return RemnawaveSignal(event=body.event, username=username)


def is_stale(sent_at: datetime, now: datetime) -> bool:
    """Событие отправлено больше 10 минут назад (или «из будущего» больше чем на 10 минут)."""
    return abs(now - sent_at) > MAX_EVENT_AGE


@dataclass(frozen=True)
class RemnawaveWebhook:
    """Приёмник вебхуков Remnawave: секрет подписи и куда передавать сигналы."""

    signals: RemnawaveSignals
    secret: str
    alerter: Alerter
    clock: Clock

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route(
            REMNAWAVE_WEBHOOK_PATH, self.receive, methods=["POST"], include_in_schema=False
        )
        return router

    async def receive(self, request: Request) -> Response:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            logger.warning("Вебхук Remnawave: тело больше %d байт — отклонён", MAX_BODY_BYTES)
            return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            logger.warning("Вебхук Remnawave: тело больше %d байт — отклонён", MAX_BODY_BYTES)
            return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        if not signature_valid(body, request.headers.get(SIGNATURE_HEADER), self.secret):
            logger.warning("Вебхук Remnawave: неверная подпись — отклонён")
            await self.alerter.alert(FORGED_WEBHOOK_ALERT, source=self.receive)
            return Response(status_code=status.HTTP_403_FORBIDDEN)
        await self._process(body)
        return Response(status_code=status.HTTP_200_OK)

    async def _process(self, body: bytes) -> None:
        """Подпись верна: разобрать и передать сигнал. Ничего не бросает — ответ всегда 200."""
        try:
            parsed = WebhookBody.model_validate(json.loads(body))
            signal = to_signal(parsed)
        except ValueError as error:
            # сюда же попадают JSONDecodeError, UnicodeDecodeError и ValidationError pydantic
            logger.warning("Вебхук Remnawave: тело не разобрано: %s", type(error).__name__)
            return
        if is_stale(parsed.timestamp, utc_now(self.clock)):
            logger.warning(
                "Вебхук Remnawave %s от %s устарел — пропускаем (догонит сверка)",
                signal.event,
                parsed.timestamp.isoformat(),
            )
            return
        try:
            await self.signals.handle(signal)
        except RemnawaveError as error:
            # Панель не дала перечитать состояние — событие догонит сверка через 5 минут
            logger.warning("Вебхук Remnawave %s не обработан: %s", signal.event, error)
