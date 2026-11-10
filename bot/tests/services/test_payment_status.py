"""Статус оплаты изменился после оплаты (задача 15): спор через банк, наш возврат, возврат
мимо бота, спор на проверке, возвраты «на проверке у платёжки».

Платёжка — поддельная (`ledger.change_status` — «Platega сменила статус»), уведомления —
`PaymentNotice` с подсказками, как их разбирает Platega.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from payment_helpers import DAYS, PaymentSetup, build_payment_setup
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus, RefundStatus
from tsproxy.db.models import Client, RefundRequest
from tsproxy.domain.constants import REFUND_REVIEW_OVERDUE
from tsproxy.integrations.payments_types import (
    PaymentNotice,
    PaymentRejected,
    PaymentStatus,
    RefundOutcome,
)
from tsproxy.integrations.remnawave_types import AccountStatus, RemnawaveUnavailable
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.payment_checks import CheckOutcome

REFUND_ID = "b7cd73dd-bab2-4e20-b463-824d3f8bbff4"


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def s(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker
) -> PaymentSetup:
    return build_payment_setup(session_factory, clock, mask)


async def granted_order(s: PaymentSetup, client: Client) -> tuple[int, str]:
    """Оплаченный заказ с выданным доступом: номер заказа и платежа."""
    order_id = await s.order(client, paid=True)
    assert await s.checks.settle(await s.load(order_id)) is CheckOutcome.CREDITED
    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED
    return order_id, await s.payment_id(order_id)


async def notify(
    s: PaymentSetup, payment_id: str, status: PaymentStatus, refund_id: str | None = None
) -> CheckOutcome:
    return await s.checks.handle_notification(PaymentNotice(payment_id, status, refund_id), None)


async def add_refund(
    s: PaymentSetup, client: Client, order_id: int, status: RefundStatus
) -> RefundRequest:
    """Возврат, который начал бот (задача 21)."""
    async with s.session_factory() as session:
        refund = RefundRequest(
            order_id=order_id,
            client_id=client.id,
            requested_at=s.clock(),
            unused_days=30,
            amount=Decimal("150"),
            status=status,
        )
        session.add(refund)
        await session.commit()
        await session.refresh(refund)
        return refund


def alert_texts(s: PaymentSetup) -> list[str]:
    return [message.text for message in s.alerts.sent]


async def account_status(s: PaymentSetup, client: Client) -> AccountStatus:
    return (await s.remnawave.get_account(client.telegram_id)).status


# --- Спор через банк ---


async def test_bank_chargeback_of_only_payment_ends_access_tells_client_and_alerts(
    s: PaymentSetup, client: Client
) -> None:
    order_id, payment_id = await granted_order(s, client)
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)

    outcome = await notify(s, payment_id, PaymentStatus.REFUNDED)

    assert outcome is CheckOutcome.STATUS_CHANGED
    # других оплаченных дней нет: после снятия дней этой оплаты доступ выключен
    assert (await account_status(s, client)) is AccountStatus.DISABLED
    assert s.messages.disputed == [(client.telegram_id, None, True)]
    [alert] = alert_texts(s)
    assert "Спор через банк" in alert and f"/client {client.telegram_id}" in alert
    assert f"#{order_id}" in alert and "доступ отключён" in alert
    assert f"/proof {order_id}" in alert  # причины знают номер заказа
    assert "<code>" in alert and "&lt;code" not in alert  # разметка не экранирована дважды
    [event] = await s.events(EventKind.PAYMENT_CHARGEBACK)
    assert event.order_id == order_id
    assert event.details is not None and event.details["access"] == "ended"


async def test_chargeback_takes_only_days_of_disputed_payment(
    s: PaymentSetup, client: Client
) -> None:
    """Решение владельца 2026-10-31: другие оплаченные месяцы у клиента остаются."""
    await granted_order(s, client)
    _, payment_id = await granted_order(s, client)
    before = (await s.remnawave.get_account(client.telegram_id)).expire_at
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)

    await notify(s, payment_id, PaymentStatus.REFUNDED)

    account = await s.remnawave.get_account(client.telegram_id)
    assert account.status is not AccountStatus.DISABLED
    assert account.expire_at == before - timedelta(days=DAYS)
    assert s.messages.disputed == [(client.telegram_id, account.expire_at, False)]
    [alert] = [text for text in alert_texts(s) if "Спор через банк" in text]
    assert "другие оплаченные дни остались" in alert


async def test_repeated_chargeback_callback_changes_nothing(
    s: PaymentSetup, client: Client
) -> None:
    """Platega шлёт колбэк до 3 раз: второй раз — ни тревоги, ни сообщения клиенту."""
    _, payment_id = await granted_order(s, client)
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)

    await notify(s, payment_id, PaymentStatus.REFUNDED)
    s.clock.advance(REFUND_REVIEW_OVERDUE)  # и после окна склейки тревог тоже
    await notify(s, payment_id, PaymentStatus.REFUNDED)

    assert len(s.messages.disputed) == 1
    assert len(alert_texts(s)) == 1
    assert len(await s.events(EventKind.PAYMENT_CHARGEBACK)) == 1
    assert (await account_status(s, client)) is AccountStatus.DISABLED


async def test_panel_down_on_chargeback_asks_admins_to_take_days_manually(
    s: PaymentSetup, client: Client
) -> None:
    _, payment_id = await granted_order(s, client)
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)
    s.remnawave.fail_next(RemnawaveUnavailable("таймаут"))

    await notify(s, payment_id, PaymentStatus.REFUNDED)

    [alert] = alert_texts(s)
    assert "снимите вручную" in alert
    assert s.messages.disputed == [(client.telegram_id, None, False)]


async def test_notification_hint_alone_changes_nothing(s: PaymentSetup, client: Client) -> None:
    """Уведомление говорит «возвращено», а платёжка — «оплачен»: верим платёжке."""
    _, payment_id = await granted_order(s, client)

    await notify(s, payment_id, PaymentStatus.REFUNDED)

    assert (await account_status(s, client)) is AccountStatus.ACTIVE
    assert alert_texts(s) == []
    assert s.payments.asked[-1] == payment_id  # спросили платёжку


# --- Наш возврат и возврат мимо бота ---


@pytest.mark.parametrize("status", [RefundStatus.APPROVED, RefundStatus.IN_REVIEW])
async def test_our_refund_result_is_quiet_and_finishes_refund(
    s: PaymentSetup, client: Client, status: RefundStatus
) -> None:
    order_id, payment_id = await granted_order(s, client)
    refund = await add_refund(s, client, order_id, status)
    await s.set_status(order_id, OrderStatus.REFUNDED)
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)

    await notify(s, payment_id, PaymentStatus.REFUNDED, REFUND_ID)

    assert s.reviews.settled == [(refund.id, RefundOutcome.SUCCEEDED)]
    assert alert_texts(s) == []
    assert s.messages.disputed == []
    assert (await account_status(s, client)) is AccountStatus.ACTIVE


async def test_finished_refund_repeat_is_quiet(s: PaymentSetup, client: Client) -> None:
    order_id, payment_id = await granted_order(s, client)
    await add_refund(s, client, order_id, RefundStatus.COMPLETED)
    await s.set_status(order_id, OrderStatus.REFUNDED)
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)

    await notify(s, payment_id, PaymentStatus.REFUNDED, REFUND_ID)

    assert s.reviews.settled == []
    assert alert_texts(s) == []


async def test_refund_from_cabinet_alerts_without_disabling(
    s: PaymentSetup, client: Client
) -> None:
    """Возврат с `refundId`, а у бота возврата нет — его сделали в кабинете Platega
    (например, после отказа API): доступ не трогаем, про спор клиенту не пишем."""
    order_id, payment_id = await granted_order(s, client)
    await add_refund(s, client, order_id, RefundStatus.FAILED)
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)

    await notify(s, payment_id, PaymentStatus.REFUNDED, REFUND_ID)
    await notify(s, payment_id, PaymentStatus.REFUNDED, REFUND_ID)

    assert (await account_status(s, client)) is AccountStatus.ACTIVE
    assert s.messages.disputed == []
    [alert] = alert_texts(s)
    assert "через кабинет платёжки" in alert
    assert len(await s.events(EventKind.PAYMENT_REFUNDED_OUTSIDE_BOT)) == 1


# --- Спор на проверке и другие статусы ---


async def test_dispute_alerts_once_and_lifted_dispute_alerts_again(
    s: PaymentSetup, client: Client
) -> None:
    order_id, payment_id = await granted_order(s, client)
    s.payments.ledger.change_status(payment_id, PaymentStatus.DISPUTED)

    await notify(s, payment_id, PaymentStatus.DISPUTED)
    await notify(s, payment_id, PaymentStatus.DISPUTED)  # повтор колбэка

    [alert] = alert_texts(s)
    paid = s.admin_texts.get("payments.status.succeeded")
    disputed = s.admin_texts.get("payments.status.disputed")
    assert f"был «{paid}», стал «{disputed}»" in alert
    assert (await account_status(s, client)) is AccountStatus.ACTIVE  # только тревога

    s.payments.ledger.change_status(payment_id, PaymentStatus.SUCCEEDED)
    await notify(s, payment_id, PaymentStatus.SUCCEEDED)  # повторный CONFIRMED — без запроса
    assert len(alert_texts(s)) == 1
    await notify(s, payment_id, PaymentStatus.DISPUTED)  # подсказка устарела — спросили
    assert f"был «{disputed}», стал «{paid}»" in alert_texts(s)[-1]
    assert len(await s.events(EventKind.PAYMENT_STATUS_CHANGED)) == 2
    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED


async def test_unpaid_order_with_dispute_is_not_credited_and_alerts_once(
    s: PaymentSetup, client: Client
) -> None:
    """Деньги на проверке у заказа, который мы не засчитывали: фоновая проверка не зачтёт
    и не будет тревожить каждые 2 минуты."""
    order_id = await s.order(client)
    s.payments.ledger.change_status(await s.payment_id(order_id), PaymentStatus.DISPUTED)

    await s.checks.check_fresh()
    await s.checks.check_fresh()

    assert (await s.load(order_id)).status is OrderStatus.CREATED
    [alert] = alert_texts(s)
    assert "неожиданно изменился" in alert
    assert s.messages.granted == []


async def test_check_button_on_disputed_order_says_problem(s: PaymentSetup, client: Client) -> None:
    order_id = await s.order(client)
    s.payments.ledger.change_status(await s.payment_id(order_id), PaymentStatus.DISPUTED)

    assert await s.checks.check_order(client.id, order_id) is CheckOutcome.STATUS_CHANGED


async def test_provider_refusing_check_raises_one_alert(s: PaymentSetup, client: Client) -> None:
    order_id = await s.order(client)
    s.payments.fail_next(PaymentRejected("Platega: HTTP 401, Auth:GEN_1001"))

    assert await s.checks.check_order(client.id, order_id) is CheckOutcome.NOT_FOUND

    [alert] = alert_texts(s)
    assert "Auth:GEN_1001" in alert and "PLATEGA_SECRET" in alert


# --- Возвраты «на проверке у платёжки» ---


async def test_refund_in_review_finished_by_background_check(
    s: PaymentSetup, client: Client
) -> None:
    """Колбэк CHARGEBACKED потерялся — бот сам узнаёт, что деньги вернулись."""
    order_id, payment_id = await granted_order(s, client)
    refund = await add_refund(s, client, order_id, RefundStatus.IN_REVIEW)
    await s.set_status(order_id, OrderStatus.REFUNDED)
    s.payments.ledger.change_status(payment_id, PaymentStatus.REFUNDED)

    await s.checks.check_refunds()

    assert s.reviews.settled == [(refund.id, RefundOutcome.SUCCEEDED)]
    assert alert_texts(s) == []


async def test_refund_stuck_in_review_alerts_once_after_a_day(
    s: PaymentSetup, client: Client
) -> None:
    """Об отказе в возврате всей суммы Platega не сообщает — через сутки одна тревога."""
    order_id, _ = await granted_order(s, client)
    refund = await add_refund(s, client, order_id, RefundStatus.IN_REVIEW)

    await s.checks.check_refunds()
    assert alert_texts(s) == []

    s.clock.advance(REFUND_REVIEW_OVERDUE)
    await s.checks.check_refunds()
    s.clock.advance(REFUND_REVIEW_OVERDUE)
    await s.checks.check_refunds()

    [alert] = alert_texts(s)
    assert f"Возврат №{refund.id}" in alert and "24 ч" in alert
    assert s.reviews.settled == []
