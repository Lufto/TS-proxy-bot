"""Клиент Platega (задача 15) на примерах ответов из документации docs.platega.io.

Сеть не трогаем: Platega подделана `httpx.MockTransport` (`MockPanel` из соседних тестов).
Ключи — выдуманные.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from mock_panel import MockPanel

from tsproxy.integrations.payments_types import (
    InvalidNotification,
    PaymentNotFound,
    PaymentNotice,
    PaymentRejected,
    PaymentRequest,
    PaymentStatus,
    PaymentUnavailable,
    RefundOutcome,
)
from tsproxy.integrations.platega import (
    FALLBACK_LINK_LIFETIME,
    PlategaPaymentProvider,
    create_platega_http_client,
    link_lifetime,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
MERCHANT = "29ef6fa6-0d2b-466c-9604-0363a30436cc"  # пример из документации
SECRET = "secret-for-tests"
RETURN_URL = "https://t.me/ts_proxy_test_bot"
PAYMENT = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
TRANSACTION = f"/transaction/{PAYMENT}"
HEADERS = {"X-MerchantId": MERCHANT, "X-Secret": SECRET}

# Ответ `POST /transaction/process` из документации
CREATED = {
    "paymentMethod": "SBPQR",
    "transactionId": "3fa85f64-5717-4562-b3fc-2c463f66afa6",
    "redirect": "https://pay.platega.io?qrsbp",
    "return": "https://google.com",
    "paymentDetails": "100 RUB",
    "status": "PENDING",
    "expiresIn": "00:15:00",
    "merchantId": "1a021d91-9b26-4762-b303-5d4aac74e921",
    "usdtRate": 93.45,
}


def transaction(status: str = "CONFIRMED", payload: str = "7", amount: Any = 150) -> dict[str, Any]:
    """Ответ `GET /transaction/{id}` из документации; статус, заказ и сумма — наши."""
    return {
        "id": PAYMENT,
        "status": status,
        "paymentDetails": {"amount": amount, "currency": "RUB"},
        "merchantName": "Demo Merchant",
        "mechantId": PAYMENT,
        "comission": 0,
        "paymentMethod": "SBPQR",
        "expiresIn": "00:15:00",
        "return": "https://example.com/success",
        "comissionUsdt": 1.64044944,
        "amountUsdt": 10.8988764,
        "qr": "base64-qr-data-or-url",
        "payformSuccessUrl": "https://pay.platega.io/success",
        "payload": payload,
        "comissionType": 1,
        "externalId": "0000a4f3-0000-0000-b8ac-fcb675a0000a",
        "description": "Оплата заказа #12345",
        "isBalanceProcessed": True,
        "person": "Иванов Иван Иванович",
        "contactPhoneNumber": "79001234567",
    }


def error(code: str, status: int, message: str = "Ошибка") -> httpx.Response:
    """Ошибка в формате документации Platega."""
    body = {
        "code": code,
        "type": 4001,
        "message": message,
        "data": [{"key": "reasonCode", "message": code}],
        "traceId": "af95e45f8c350d4211ac5c7b6d2e1f04",
    }
    return httpx.Response(status, json=body)


SUPPORTED = {
    "supported": True,
    "partialSupported": True,
    "totalDeductUsdt": 0.01236094,
    "penaltyNativeAmount": None,
    "penaltyNativeCurrency": None,
    "penaltyUsdt": None,
    "penaltyConversionRate": None,
    "blockReason": None,
}
CANCEL_ACCEPTED = {
    "transactionId": PAYMENT,
    "accepted": True,
    "manualControlRequired": False,
    "message": None,
    "refundOperationId": "b7cd73dd-bab2-4e20-b463-824d3f8bbff4",
}


@pytest.fixture
def platega() -> MockPanel:
    return MockPanel()


def make(platega: MockPanel, *, send_customer_email: bool = False) -> PlategaPaymentProvider:
    http = create_platega_http_client(transport=httpx.MockTransport(platega.handle))
    return PlategaPaymentProvider(
        http,
        MERCHANT,
        SECRET,
        RETURN_URL,
        lambda: NOW,
        test=True,
        send_customer_email=send_customer_email,
    )


def request(amount: str = "150") -> PaymentRequest:
    return PaymentRequest(7, Decimal(amount), "Подписка TS Proxy: 1 месяц", "key-7", 555)


# --- Создание платежа ---


async def test_create_payment_sends_sbp_order_and_return_to_bot(platega: MockPanel) -> None:
    platega.on("POST", "/transaction/process", httpx.Response(200, json=CREATED))

    created = await make(platega).create_payment(request())

    assert created.payment_id == CREATED["transactionId"]
    assert created.url == "https://pay.platega.io?qrsbp"
    assert created.expires_at == NOW + timedelta(minutes=15)  # из `expiresIn`, а не час
    [sent] = platega.requests
    assert sent.headers["X-MerchantId"] == MERCHANT
    assert sent.headers["X-Secret"] == SECRET
    assert json.loads(sent.content) == {
        "paymentMethod": 2,
        "paymentDetails": {"amount": 150, "currency": "RUB"},
        "description": "Подписка TS Proxy: 1 месяц",
        "return": RETURN_URL,
        "failedUrl": RETURN_URL,
        "orderId": "7",
        "payload": "7",
    }  # без metadata и email (решение владельца 2026-10-31)


async def test_customer_email_is_one_switch_away(platega: MockPanel) -> None:
    """Если Platega потребует email — включить `send_customer_email`: Telegram ID @telegram.org."""
    platega.on("POST", "/transaction/process", httpx.Response(200, json=CREATED))

    await make(platega, send_customer_email=True).create_payment(request())

    assert platega.body()["metadata"] == {"user_email": "555@telegram.org"}


async def test_kopecks_are_sent_as_number(platega: MockPanel) -> None:
    platega.on("POST", "/transaction/process", httpx.Response(200, json=CREATED))

    await make(platega).create_payment(request("149.90"))

    assert platega.body()["paymentDetails"]["amount"] == 149.9


async def test_below_platega_minimum_is_refused_without_request(platega: MockPanel) -> None:
    with pytest.raises(PaymentRejected, match="от 50"):
        await make(platega).create_payment(request("49"))
    assert platega.requests == []


async def test_platega_refusal_text_goes_to_alert(platega: MockPanel) -> None:
    """Например, если Platega начнёт требовать email: текст отказа — в тревогу."""
    platega.on(
        "POST", "/transaction/process", error("VALIDATION", 400, "metadata.user_email required")
    )

    with pytest.raises(PaymentRejected) as raised:
        await make(platega).create_payment(request())

    text = str(raised.value)
    assert "VALIDATION" in text and "metadata.user_email required" in text
    assert "af95e45f8c350d4211ac5c7b6d2e1f04" in text  # traceId — для поддержки Platega


async def test_wrong_keys_are_refusal(platega: MockPanel) -> None:
    platega.on("POST", "/transaction/process", error("Auth:GEN_1001", 401, "Access denied."))

    with pytest.raises(PaymentRejected, match="Access denied"):
        await make(platega).create_payment(request())


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(500),
        httpx.Response(429, json={"code": "Common:TMR_0001"}),
        httpx.Response(200, text="<html>bad gateway</html>"),
        httpx.Response(200, json={"status": "PENDING"}),  # не тот формат
    ],
)
async def test_unavailable_platega(platega: MockPanel, reply: httpx.Response) -> None:
    platega.on("POST", "/transaction/process", reply)

    with pytest.raises(PaymentUnavailable):
        await make(platega).create_payment(request())


async def test_network_failure_is_unavailable(platega: MockPanel) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("таймаут", request=request)

    platega.on("POST", "/transaction/process", timeout)

    with pytest.raises(PaymentUnavailable):
        await make(platega).create_payment(request())


@pytest.mark.parametrize(
    ("raw", "lifetime"),
    [
        ("00:30:00", timedelta(minutes=30)),
        ("00:15:00", timedelta(minutes=15)),
        ("1.00:00:00", timedelta(days=1)),
        ("00:30:00.0000000", timedelta(minutes=30)),
        (None, FALLBACK_LINK_LIFETIME),
        ("полчаса", FALLBACK_LINK_LIFETIME),
        ("00:00:00", FALLBACK_LINK_LIFETIME),
    ],
)
def test_link_lifetime_from_expires_in(raw: str | None, lifetime: timedelta) -> None:
    assert link_lifetime(raw) == lifetime


# --- Статус ---


async def test_get_payment_reads_status_amount_and_order(platega: MockPanel) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction(amount=150.25)))

    state = await make(platega).get_payment(PAYMENT)

    assert state.status is PaymentStatus.SUCCEEDED
    assert state.amount == Decimal("150.25")  # без float
    assert (state.currency, state.order_id, state.test) == ("RUB", 7, True)
    assert state.provider_status == "CONFIRMED"
    assert state.refunded == 0


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("PENDING", PaymentStatus.PENDING),
        ("CANCELED", PaymentStatus.CANCELED),
        ("CHARGEBACKED", PaymentStatus.REFUNDED),
        ("PARTIALLY_REFUNDED", PaymentStatus.PARTIALLY_REFUNDED),
        ("DISPUTED", PaymentStatus.DISPUTED),
        ("SOMETHING_NEW", PaymentStatus.UNKNOWN),
    ],
)
async def test_platega_statuses(platega: MockPanel, raw: str, status: PaymentStatus) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction(raw)))

    state = await make(platega).get_payment(PAYMENT)

    assert state.status is status
    assert state.status_name == raw


async def test_chargebacked_payment_is_refunded_in_full(platega: MockPanel) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction("CHARGEBACKED")))

    assert (await make(platega).get_payment(PAYMENT)).refunded == Decimal("150")


async def test_foreign_payload_gives_no_order(platega: MockPanel) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction(payload="custom-payload")))

    assert (await make(platega).get_payment(PAYMENT)).order_id is None


async def test_unknown_payment(platega: MockPanel) -> None:
    platega.on("GET", TRANSACTION, error("Common:NF_0001", 404, "Transaction not found."))

    with pytest.raises(PaymentNotFound):
        await make(platega).get_payment(PAYMENT)


# --- Возврат ---


async def test_refund_checks_support_then_cancels(platega: MockPanel) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction()))
    platega.on("GET", f"{TRANSACTION}/cancel-supported", httpx.Response(200, json=SUPPORTED))
    platega.on("POST", f"{TRANSACTION}/cancel", httpx.Response(200, json=CANCEL_ACCEPTED))

    refund = await make(platega).refund(PAYMENT, Decimal("150"), "refund-request-1")

    assert refund.outcome is RefundOutcome.PENDING  # итог придёт колбэком CHARGEBACKED
    assert refund.refund_id == CANCEL_ACCEPTED["refundOperationId"]
    cancel = platega.requests[-1]
    assert cancel.content == b""  # сумму эта ручка не принимает


async def test_refund_sent_to_manual_check(platega: MockPanel) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction()))
    platega.on("GET", f"{TRANSACTION}/cancel-supported", httpx.Response(200, json=SUPPORTED))
    manual = {**CANCEL_ACCEPTED, "manualControlRequired": True}
    platega.on("POST", f"{TRANSACTION}/cancel", httpx.Response(200, json=manual))

    refund = await make(platega).refund(PAYMENT, Decimal("150"), "refund-request-1")

    assert refund.outcome is RefundOutcome.IN_REVIEW


async def test_refund_without_balance_is_refused_before_cancel(platega: MockPanel) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction()))
    blocked = {**SUPPORTED, "supported": False, "blockReason": "INSUFFICIENT_FUNDS"}
    platega.on("GET", f"{TRANSACTION}/cancel-supported", httpx.Response(200, json=blocked))

    with pytest.raises(PaymentRejected, match="INSUFFICIENT_FUNDS"):
        await make(platega).refund(PAYMENT, Decimal("150"), "refund-request-1")
    assert all(r.method == "GET" for r in platega.requests)


async def test_refund_repeat_after_success_returns_money_once(platega: MockPanel) -> None:
    """Повтор после сбоя: платёж уже CHARGEBACKED — второй раз не возвращаем."""
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction("CHARGEBACKED")))

    refund = await make(platega).refund(PAYMENT, Decimal("150"), "refund-request-1")

    assert refund.outcome is RefundOutcome.SUCCEEDED
    assert len(platega.requests) == 1


async def test_refund_already_running_is_not_an_error(platega: MockPanel) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction()))
    platega.on("GET", f"{TRANSACTION}/cancel-supported", httpx.Response(200, json=SUPPORTED))
    platega.on("POST", f"{TRANSACTION}/cancel", error("REFUND_IN_PROGRESS", 409))

    refund = await make(platega).refund(PAYMENT, Decimal("150"), "refund-request-1")

    assert refund.outcome is RefundOutcome.PENDING


@pytest.mark.parametrize(
    ("status", "amount"), [("CANCELED", "150"), ("DISPUTED", "150"), ("CONFIRMED", "100")]
)
async def test_refund_only_full_and_only_paid(platega: MockPanel, status: str, amount: str) -> None:
    platega.on("GET", TRANSACTION, httpx.Response(200, json=transaction(status)))

    with pytest.raises(PaymentRejected):
        await make(platega).refund(PAYMENT, Decimal(amount), "refund-request-1")
    assert len(platega.requests) == 1


# --- Колбэк ---

# Колбэк CONFIRMED из документации (наш `payload` — номер заказа)
CALLBACK = {
    "id": PAYMENT,
    "amount": 1500.25,
    "currency": "RUB",
    "status": "CONFIRMED",
    "paymentMethod": 2,
    "payload": "order-8089",
}


def test_callback_with_our_keys(platega: MockPanel) -> None:
    body = json.dumps(CALLBACK).encode()

    notice = make(platega).parse_notification(body, {**HEADERS, "User-Agent": "Platega Payment"})

    assert notice == PaymentNotice(PAYMENT, PaymentStatus.SUCCEEDED)
    assert platega.requests == []  # сеть не трогаем


def test_refund_callback_carries_refund_id(platega: MockPanel) -> None:
    """Возврат продавца: `refundId`; спор через банк — без него (документация CHARGEBACKED)."""
    ours = {
        **CALLBACK,
        "status": "CHARGEBACKED",
        "refundId": "b7cd73dd-bab2-4e20-b463-824d3f8bbff4",
        "refundAmount": 1500.25,
        "refundStatus": "Succeeded",
    }
    bank = {**CALLBACK, "status": "CHARGEBACKED"}
    provider = make(platega)

    assert provider.parse_notification(json.dumps(ours).encode(), HEADERS).refund_id == (
        "b7cd73dd-bab2-4e20-b463-824d3f8bbff4"
    )
    assert provider.parse_notification(json.dumps(bank).encode(), HEADERS) == PaymentNotice(
        PAYMENT, PaymentStatus.REFUNDED
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-MerchantId": MERCHANT},
        {"X-Secret": SECRET},
        {"X-MerchantId": MERCHANT, "X-Secret": "guess"},
        {"X-MerchantId": "other", "X-Secret": SECRET},
    ],
)
def test_callback_without_our_keys_is_forged(platega: MockPanel, headers: dict[str, str]) -> None:
    with pytest.raises(InvalidNotification):
        make(platega).parse_notification(json.dumps(CALLBACK).encode(), headers)


@pytest.mark.parametrize(
    "body", [b"", b"[]", b'{"status": "CONFIRMED"}', b'{"id": "", "status": "X"}']
)
def test_broken_callback_is_rejected(platega: MockPanel, body: bytes) -> None:
    with pytest.raises(InvalidNotification):
        make(platega).parse_notification(body, HEADERS)


# --- Сверка ---

# Ответ `POST /transaction/export/json` из документации (статусы и `payload` — наши)
EXPORT = [
    {
        "recordId": "486c22ef-3524-4a1c-9740-3fe8c3e859d9",
        "createdAt": "2026-06-15 13:44:13",
        "amount": 150,
        "currencyCode": "RUB",
        "status": "CONFIRMED",
        "paymentMethod": "SBPQR",
        "description": "1234",
        "payload": "7",
    },
    {
        "recordId": "4a2f32f8-2f9c-40e5-be08-7d3fb86b1c58",
        "createdAt": "2026-06-14 17:27:44",
        "amount": 1.15,
        "currencyCode": "RUB",
        "status": "CHARGEBACKED",
        "paymentMethod": "SBPQR",
        "description": "1234",
        "payload": "",
    },
]


async def test_list_payments_from_export(platega: MockPanel) -> None:
    platega.on("POST", "/transaction/export/json", httpx.Response(200, json=EXPORT))

    states = await make(platega).list_payments(NOW - timedelta(days=1), NOW)

    assert [(s.payment_id, s.status, s.order_id) for s in states] == [
        ("486c22ef-3524-4a1c-9740-3fe8c3e859d9", PaymentStatus.SUCCEEDED, 7),
        ("4a2f32f8-2f9c-40e5-be08-7d3fb86b1c58", PaymentStatus.REFUNDED, None),
    ]
    assert states[1].amount == Decimal("1.15")
    assert platega.body() == {
        "from": "2026-10-30T12:00:00.000Z",
        "to": "2026-10-31T12:00:00.000Z",
        "timeZoneId": "UTC",
    }


async def test_export_link_instead_of_list_is_refusal(platega: MockPanel) -> None:
    """В описании ручки сказано «возвращает ссылку на файл» — такой ответ бот не понимает."""
    platega.on("POST", "/transaction/export/json", httpx.Response(200, json="https://file"))

    with pytest.raises(PaymentRejected, match="не список"):
        await make(platega).list_payments(NOW - timedelta(days=1), NOW)
