"""Platega — настоящая платёжка за интерфейсом `PaymentProvider` (задача 15).

Документация: https://docs.platega.io/llms.txt (прочитана 2026-10-31). Что делаем:

- **Платёж** — `POST /transaction/process`: СБП (`paymentMethod: 2`), `orderId` и `payload` —
  номер нашего заказа, `return` и `failedUrl` — прямо в бота `https://t.me/<бот>` (сайт
  не должен перебрасывать в бота — менеджер Platega). `metadata` не передаём, email клиента
  тоже (решение владельца 2026-10-31). Если Platega его потребует — включить
  `send_customer_email`: бот передаст `{telegram_id}@telegram.org`, как советует документация
  для магазинов в Telegram. Срок ссылки — из `expiresIn` ответа (обычно 30 минут).
  Ключа идемпотентности у Platega нет: повтор после таймаута может создать второй платёж,
  но его ссылку клиент не увидит — оплатить его нельзя.
- **Статус** — `GET /transaction/{id}`. Номер заказа в ответе — только в `payload`
  (поля `orderId` в ответе статуса нет), его и сверяем с заказом.
- **Уведомление** (колбэк) без подписи: подлинность — заголовки `X-MerchantId` и `X-Secret`
  (наши же ключи). Из тела берём номер платежа и подсказки: статус и `refundId` (он есть
  только у возвратов, которые сделал продавец). Правду бот всегда узнаёт `GET`-запросом.
- **Возврат** — только всей суммы: `GET …/cancel-supported` (хватает ли баланса), затем
  `POST …/cancel`. Итог приходит колбэком `CHARGEBACKED`, поэтому принятый возврат —
  «ждёт итога» (`RefundOutcome.PENDING`), а `manualControlRequired` — «на проверке».
  Повтор безопасен: идущий возврат Platega не задваивает, а уже вернувшийся платёж
  (`CHARGEBACKED`) бот видит до запроса и второй раз не возвращает.
- **Сверка** — `POST /transaction/export/json` за сутки (задача 14).

Тестового режима у Platega нет: тестовый кабинет — отдельные ключи. Признак «тестовый»
у платежей — из настройки `PLATEGA_TEST_ACCOUNT` (аудит R08: в бою засчитываются только
настоящие платежи, на Mac — только тестовые).

Суммы в ответах читаются сразу в `Decimal` (`parse_float=Decimal`), без `float`.
"""

import hmac
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tsproxy.domain.dates import require_aware
from tsproxy.integrations.failures import Failure
from tsproxy.integrations.payments_types import (
    RUB,
    CreatedPayment,
    InvalidNotification,
    PaymentFailure,
    PaymentNotFound,
    PaymentNotice,
    PaymentRejected,
    PaymentRequest,
    PaymentState,
    PaymentStatus,
    PaymentUnavailable,
    Refund,
    RefundOutcome,
)

logger = logging.getLogger(__name__)

PLATEGA_PROVIDER = "platega"
PLATEGA_BASE_URL = "https://app.platega.io"
MERCHANT_HEADER = "X-MerchantId"
SECRET_HEADER = "X-Secret"  # noqa: S105 — имя заголовка, а не сам секрет
USER_AGENT = "TSProxyBot/1.0 (+https://ts-proxy.com)"
# Platega ждёт ответа на колбэк 30–60 с; нашим запросам хватит 15 с
REQUEST_TIMEOUT_SECONDS = 15.0
SBP_METHOD = 2  # `paymentMethod`: СБП (QR и ссылка в банковское приложение)
# Минимальная сумма платежа у Platega (ответ менеджера 2026-10-30)
MIN_AMOUNT = Decimal("50")
# Документация советует для магазинов в Telegram email вида `{telegram_id}@telegram.org`
TELEGRAM_EMAIL_DOMAIN = "telegram.org"
# `expiresIn` не разобран — считаем, что ссылка живёт 15 минут (пример из документации):
# так бот не отдаст повторно ссылку, которая уже могла истечь
FALLBACK_LINK_LIFETIME = timedelta(minutes=15)
EXPIRES_IN = re.compile(r"^(?:(?P<days>\d+)\.)?(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})(?:\.\d+)?$")
# Номер платежа из колбэка не длиннее, чем помещается в базу (`orders.payment_id`)
MAX_PAYMENT_ID = 128
REFUND_IN_PROGRESS = "REFUND_IN_PROGRESS"  # 409: по платежу уже идёт возврат
EXPORT_TIMEZONE = "UTC"

HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
HTTP_CLIENT_ERROR = 400
HTTP_AUTH_ERRORS = (401, 403)  # ключи магазина не приняты

# Статусы Platega → наши. Незнакомый — `UNKNOWN` (бот поднимет тревогу, если заказ оплачен)
STATUSES: Mapping[str, PaymentStatus] = {
    "PENDING": PaymentStatus.PENDING,
    "CONFIRMED": PaymentStatus.SUCCEEDED,
    "CANCELED": PaymentStatus.CANCELED,
    "CHARGEBACKED": PaymentStatus.REFUNDED,
    "PARTIALLY_REFUNDED": PaymentStatus.PARTIALLY_REFUNDED,
    "DISPUTED": PaymentStatus.DISPUTED,
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Created(_Model):
    transactionId: str = Field(min_length=1, max_length=MAX_PAYMENT_ID)
    redirect: str = Field(pattern=r"^https://")
    expiresIn: str | None = None


class _Details(_Model):
    amount: Decimal
    currency: str


class _Transaction(_Model):
    id: str
    status: str
    paymentDetails: _Details
    payload: str | None = None


class _CancelSupported(_Model):
    supported: bool
    blockReason: str | None = None


class _Cancel(_Model):
    accepted: bool
    manualControlRequired: bool = False
    message: str | None = None
    refundOperationId: str | None = None


class _Callback(_Model):
    id: str = Field(min_length=1, max_length=MAX_PAYMENT_ID)
    status: str
    refundId: str | None = Field(default=None, max_length=MAX_PAYMENT_ID)


class _ExportRecord(_Model):
    recordId: str
    status: str
    amount: Decimal
    currencyCode: str
    payload: str | None = None


class _Error(_Model):
    code: str | None = None
    message: str | None = None
    traceId: str | None = None


def create_platega_http_client(
    base_url: str = PLATEGA_BASE_URL, *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """HTTP-клиент к Platega. Ключи сюда не кладём — их добавляет `PlategaPaymentProvider`."""
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        transport=transport,
    )


class PlategaPaymentProvider:
    """Platega за интерфейсом `PaymentProvider`."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        merchant_id: str,
        secret: str,
        return_url: str,
        clock: Callable[[], datetime],
        *,
        test: bool,  # тестовый кабинет Platega (`PLATEGA_TEST_ACCOUNT`)
        send_customer_email: bool = False,  # решение владельца 2026-10-31: пока не передаём
    ) -> None:
        self._http = http
        self._merchant_id = merchant_id
        self._secret = secret
        self._return_url = return_url
        self._clock = clock
        self._test = test
        self._send_customer_email = send_customer_email

    @property
    def name(self) -> str:
        return PLATEGA_PROVIDER

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- Платёж ---

    async def create_payment(self, request: PaymentRequest) -> CreatedPayment:
        """Ссылка на оплату по СБП. Отказ Platega — `PaymentRejected` с её текстом."""
        if request.amount < MIN_AMOUNT:
            raise PaymentRejected(
                f"Platega принимает от {MIN_AMOUNT} ₽, а заказ на {request.amount}"
            )
        now = require_aware(self._clock())
        response = await self._send(
            "POST", "/transaction/process", body=self._payment_body(request)
        )
        created = _parse(_Created, _json(_checked(response)))
        logger.info("Platega: платёж %s по заказу %s", created.transactionId, request.order_id)
        return CreatedPayment(
            payment_id=created.transactionId,
            url=created.redirect,
            expires_at=now + link_lifetime(created.expiresIn),
        )

    def _payment_body(self, request: PaymentRequest) -> dict[str, Any]:
        order = str(request.order_id)
        body: dict[str, Any] = {
            "paymentMethod": SBP_METHOD,
            "paymentDetails": {"amount": _json_amount(request.amount), "currency": RUB},
            "description": request.description,
            "return": self._return_url,
            "failedUrl": self._return_url,
            "orderId": order,
            # номер заказа в ответе статуса есть только здесь — по нему сверяем платёж с заказом
            "payload": order,
        }
        metadata = self._metadata(request)
        if metadata is not None:
            body["metadata"] = metadata
        return body

    def _metadata(self, request: PaymentRequest) -> dict[str, str] | None:
        """Email клиента для Platega — только если включён `send_customer_email`."""
        if not self._send_customer_email or request.telegram_id is None:
            return None
        return {"user_email": f"{request.telegram_id}@{TELEGRAM_EMAIL_DOMAIN}"}

    async def get_payment(self, payment_id: str) -> PaymentState:
        response = await self._send("GET", f"/transaction/{payment_id}")
        transaction = _parse(_Transaction, _json(_checked(response)))
        return self._state(
            transaction.id,
            transaction.status,
            transaction.paymentDetails.amount,
            transaction.paymentDetails.currency,
            transaction.payload,
        )

    def _state(
        self, payment_id: str, raw_status: str, amount: Decimal, currency: str, payload: str | None
    ) -> PaymentState:
        status = STATUSES.get(raw_status, PaymentStatus.UNKNOWN)
        return PaymentState(
            payment_id=payment_id,
            status=status,
            amount=amount,
            currency=currency,
            order_id=_order_id(payload),
            test=self._test,
            paid_at=None,  # Platega время оплаты не сообщает — бот берёт время зачёта
            # частичных возвратов у нас нет; сколько вернули частично, статус не сообщает
            refunded=amount if status is PaymentStatus.REFUNDED else Decimal(0),
            provider_status=raw_status,
        )

    # --- Возврат ---

    async def refund(self, payment_id: str, amount: Decimal, idempotency_key: str) -> Refund:
        """Вернуть всю сумму. `idempotency_key` Platega не нужен: повтор безопасен и так."""
        state = await self.get_payment(payment_id)
        if state.status is PaymentStatus.REFUNDED:
            logger.info("Platega: платёж %s уже возвращён — повтор", payment_id)
            return Refund(payment_id, payment_id, state.amount, RefundOutcome.SUCCEEDED)
        if state.status is not PaymentStatus.SUCCEEDED:
            raise PaymentRejected(f"платёж в статусе {state.status_name} — вернуть нельзя")
        if amount != state.amount:
            raise PaymentRejected(
                f"Platega возвращает только всю сумму: оплачено {state.amount}, просим {amount}"
            )
        check = await self._send("GET", f"/transaction/{payment_id}/cancel-supported")
        supported = _parse(_CancelSupported, _json(_checked(check)))
        if not supported.supported:
            raise PaymentRejected(f"Platega: возврат недоступен ({supported.blockReason})")
        response = await self._send("POST", f"/transaction/{payment_id}/cancel")
        if response.status_code == HTTP_CONFLICT and _error(response).code == REFUND_IN_PROGRESS:
            logger.info("Platega: по платежу %s возврат уже идёт", payment_id)
            return Refund("", payment_id, amount, RefundOutcome.PENDING)
        cancel = _parse(_Cancel, _json(_checked(response)))
        outcome = RefundOutcome.IN_REVIEW if cancel.manualControlRequired else RefundOutcome.PENDING
        logger.info("Platega: возврат по %s принят (%s): %s", payment_id, outcome, cancel.message)
        return Refund(cancel.refundOperationId or "", payment_id, amount, outcome)

    # --- Уведомление ---

    def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> PaymentNotice:
        """Колбэк Platega: свои ли ключи в заголовках, затем номер платежа и подсказки."""
        if not (
            _same(headers.get(MERCHANT_HEADER), self._merchant_id)
            and _same(headers.get(SECRET_HEADER), self._secret)
        ):
            raise InvalidNotification("колбэк без наших X-MerchantId / X-Secret")
        try:
            callback = _Callback.model_validate_json(body)
        except ValidationError as error:
            raise InvalidNotification(f"колбэк не разобран: {error.error_count()} ошибок") from None
        return PaymentNotice(
            callback.id, STATUSES.get(callback.status, PaymentStatus.UNKNOWN), callback.refundId
        )

    # --- Сверка ---

    async def list_payments(self, since: datetime, until: datetime) -> Sequence[PaymentState]:
        body = {
            "from": _export_time(since),
            "to": _export_time(until),
            "timeZoneId": EXPORT_TIMEZONE,
        }
        response = await self._send("POST", "/transaction/export/json", body=body)
        data = _json(_checked(response))
        if not isinstance(data, list):
            message = "выгрузка Platega вернула не список платежей"
            raise PaymentRejected(
                message, failure=Failure(PaymentFailure.BAD_RESPONSE, {"error": message})
            )
        records = [_parse(_ExportRecord, item) for item in data]
        return [
            self._state(r.recordId, r.status, r.amount, r.currencyCode, r.payload) for r in records
        ]

    # --- HTTP ---

    async def _send(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Запрос с нашими ключами. Сеть, таймаут, 5xx и 429 — `PaymentUnavailable`."""
        headers = {MERCHANT_HEADER: self._merchant_id, SECRET_HEADER: self._secret}
        try:
            response = await self._http.request(method, path, json=body, headers=headers)
        except httpx.HTTPError as error:
            raise PaymentUnavailable(
                f"Platega не ответила: {type(error).__name__}",
                failure=Failure(
                    PaymentFailure.NO_CONNECTION,
                    {"error": type(error).__name__, "seconds": int(REQUEST_TIMEOUT_SECONDS)},
                ),
            ) from error
        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            raise PaymentUnavailable(
                "Platega: HTTP 429", failure=Failure(PaymentFailure.RATE_LIMITED)
            )
        if response.status_code >= HTTP_SERVER_ERROR:
            raise PaymentUnavailable(
                f"Platega: HTTP {response.status_code}",
                failure=Failure(PaymentFailure.SERVER_ERROR, {"status": response.status_code}),
            )
        return response


def link_lifetime(expires_in: str | None) -> timedelta:
    """`expiresIn` («00:30:00», «1.00:00:00») → сколько живёт ссылка."""
    match = None if expires_in is None else EXPIRES_IN.fullmatch(expires_in.strip())
    if match is None:
        logger.warning("Platega: срок ссылки %r не разобран — считаем 15 минут", expires_in)
        return FALLBACK_LINK_LIFETIME
    lifetime = timedelta(
        days=int(match["days"] or 0),
        hours=int(match["h"]),
        minutes=int(match["m"]),
        seconds=int(match["s"]),
    )
    return lifetime if lifetime > timedelta(0) else FALLBACK_LINK_LIFETIME


def _checked(response: httpx.Response) -> httpx.Response:
    """Ответ с ошибкой — исключение: 404 — нет платежа, остальные 4xx — отказ с текстом."""
    if response.status_code == HTTP_NOT_FOUND:
        raise PaymentNotFound(f"Platega: {_error_text(response)}")
    if response.status_code >= HTTP_CLIENT_ERROR:
        text = _error_text(response)
        kind = (
            PaymentFailure.KEYS_REJECTED
            if response.status_code in HTTP_AUTH_ERRORS
            else PaymentFailure.PLATEGA_REJECTED
        )
        raise PaymentRejected(f"Platega: {text}", failure=Failure(kind, {"error": text}))
    return response


def _error(response: httpx.Response) -> _Error:
    try:
        return _Error.model_validate_json(response.content)
    except ValidationError:
        return _Error()


def _error_text(response: httpx.Response) -> str:
    """Текст ошибки для тревоги: код, пояснение и `traceId` (по нему ищет поддержка Platega)."""
    error = _error(response)
    parts = [f"HTTP {response.status_code}"]
    parts.extend(part for part in (error.code, error.message) if part)
    if error.traceId:
        parts.append(f"traceId {error.traceId}")
    return ", ".join(parts)


def _json(response: httpx.Response) -> Any:
    try:
        return json.loads(response.content, parse_float=Decimal)
    except ValueError:
        message = f"ответ не JSON (HTTP {response.status_code})"
        raise PaymentUnavailable(
            f"Platega: {message}", failure=Failure(PaymentFailure.BAD_RESPONSE, {"error": message})
        ) from None


def _parse[M: BaseModel](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        # формат ответа не тот, что в документации — повторим позже, в логе подробности
        logger.error("Platega: ответ не разобран (%s): %s", model.__name__, error)
        message = f"ответ не разобран ({model.__name__})"
        raise PaymentUnavailable(
            f"Platega: {message}", failure=Failure(PaymentFailure.BAD_RESPONSE, {"error": message})
        ) from None


def _json_amount(amount: Decimal) -> int | float:
    """Сумма числом JSON. `float` только для записи в запрос: считаем всё в `Decimal`."""
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def _order_id(payload: str | None) -> int | None:
    """Номер заказа из `payload`; чужое или пустое — `None` (зачёт такой платёж не примет)."""
    if payload is None or not payload.isdecimal():
        return None
    return int(payload)


def _same(received: str | None, expected: str) -> bool:
    return received is not None and hmac.compare_digest(received.encode(), expected.encode())


def _export_time(moment: datetime) -> str:
    return require_aware(moment).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
