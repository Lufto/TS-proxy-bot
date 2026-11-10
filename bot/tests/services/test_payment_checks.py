"""Проверка оплат (задача 14): уведомления, «Проверить оплату», фоновая проверка в два темпа.

«Готово, когда» — все случаи задачи 04: повтор уведомления, оплата старого платежа,
подделка, потерянное уведомление.
"""

from datetime import timedelta

import pytest
from payment_helpers import PaymentSetup, build_payment_setup, flaky_commits
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client
from tsproxy.domain.constants import FRESH_ORDER_AGE, PAYMENT_LINK_LIFETIME, STALE_ORDER_AGE
from tsproxy.integrations.payments_types import PaymentNotice, PaymentUnavailable
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.payment_checks import CheckOutcome, ForgedReason

SOURCE = "203.0.113.7"


async def notice_of(s: PaymentSetup, order_id: int) -> PaymentNotice:
    """Уведомление о платеже заказа без подсказки о статусе."""
    return PaymentNotice(await s.payment_id(order_id))


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def s(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker
) -> PaymentSetup:
    return build_payment_setup(session_factory, clock, mask)


@pytest.fixture
async def other(session: AsyncSession, clock: FakeClock) -> Client:
    other = Client(telegram_id=222, name="Пётр", first_seen_at=clock())
    session.add(other)
    await session.commit()
    return other


# --- Уведомление платёжки ---


async def test_notification_of_paid_order_credits_it(s: PaymentSetup, client: Client) -> None:
    order_id = await s.order(client, paid=True)

    outcome = await s.checks.handle_notification(await notice_of(s, order_id), SOURCE)

    assert outcome is CheckOutcome.CREDITED
    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED
    [granted] = s.messages.granted
    assert granted.order_id == order_id


async def test_repeated_notification_is_dropped_without_asking_provider(
    s: PaymentSetup, client: Client
) -> None:
    """Б2: платёжка прислала уведомление дважды — одно сообщение клиенту, один запрос."""
    order_id = await s.order(client, paid=True)
    payment_id = await s.payment_id(order_id)
    await s.checks.handle_notification(PaymentNotice(payment_id), SOURCE)

    outcome = await s.checks.handle_notification(PaymentNotice(payment_id), SOURCE)

    assert outcome is CheckOutcome.ALREADY_CREDITED
    assert s.payments.asked == [payment_id]
    assert len(s.messages.granted) == 1
    assert await s.events(EventKind.FAKE_PAYMENT_NOTIFICATION) == []


async def test_forged_notifications_glue_into_one_record_and_one_alert(
    s: PaymentSetup, client: Client
) -> None:
    """Б6: выдуманные номера — к платёжке не идём, одна запись журнала и одна тревога."""
    await s.order(client)
    for number in range(3):
        outcome = await s.checks.handle_notification(PaymentNotice(f"fake_forged_{number}"), SOURCE)
        assert outcome is CheckOutcome.NOT_FOUND
    await s.checks.record_forged(ForgedReason.UNPARSED, None, SOURCE)

    assert s.payments.asked == []
    [event] = await s.events(EventKind.FAKE_PAYMENT_NOTIFICATION)
    assert event.repeat_count == 4
    assert event.details == {
        "reason": "unknown_payment",
        "payment_id": "fake_forged_0",
        "source": SOURCE,
    }
    [alert] = s.alerts.sent
    assert s.admin_texts.get("alert.payment_notification_forged.title") in alert.text
    assert s.admin_texts.get("forged_reason.unknown_payment") in alert.text
    assert s.admin_texts.get("alert.payment_notification_forged.causes.1.cause") in alert.text


async def test_forged_paid_notification_for_real_unpaid_order_changes_nothing(
    s: PaymentSetup, client: Client
) -> None:
    """Б7: настоящий номер неоплаченного заказа — перепроверка видит «не оплачен»."""
    order_id = await s.order(client)

    outcome = await s.checks.handle_notification(await notice_of(s, order_id), SOURCE)

    assert outcome is CheckOutcome.NOT_PAID
    assert (await s.load(order_id)).status is OrderStatus.CREATED
    assert s.messages.granted == []


async def test_forged_cancel_notification_does_not_close_paid_order(
    s: PaymentSetup, client: Client
) -> None:
    """Б8: «отмену» тоже перепроверяем — по ответу платёжки заказ оплачен."""
    order_id = await s.order(client, paid=True)

    outcome = await s.checks.handle_notification(await notice_of(s, order_id), SOURCE)

    assert outcome is CheckOutcome.CREDITED


async def test_late_payment_of_canceled_order_is_credited(s: PaymentSetup, client: Client) -> None:
    """Уязвимость 2: заказ закрыт как истёкший, а оплата пришла позже — засчитываем."""
    order_id = await s.order(client, paid=True)
    await s.set_status(order_id, OrderStatus.CANCELED)
    s.clock.advance(timedelta(days=2))

    outcome = await s.checks.handle_notification(await notice_of(s, order_id), SOURCE)

    assert outcome is CheckOutcome.CREDITED
    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED


async def test_test_payment_in_production_is_not_credited(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    """Уязвимость 11: признак «тестовый» должен совпасть с режимом."""
    s = build_payment_setup(session_factory, clock, mask, test_payments=False)
    order_id = await s.order(client, paid=True)

    outcome = await s.checks.handle_notification(await notice_of(s, order_id), SOURCE)

    assert outcome is CheckOutcome.REJECTED
    assert (await s.load(order_id)).status is OrderStatus.CREATED
    [alert] = s.alerts.sent
    assert f"#{order_id}" in alert.text


async def test_provider_down_on_notification_leaves_order_for_later(
    s: PaymentSetup, client: Client
) -> None:
    order_id = await s.order(client, paid=True)
    s.payments.fail_next(PaymentUnavailable("таймаут"))

    with pytest.raises(PaymentUnavailable):
        await s.checks.handle_notification(await notice_of(s, order_id), SOURCE)

    assert (await s.load(order_id)).status is OrderStatus.CREATED


# --- Потерянное уведомление: фоновая проверка ---


async def test_lost_notification_is_found_by_fresh_check(s: PaymentSetup, client: Client) -> None:
    """Б3: уведомления нет — фоновая проверка через пару минут засчитывает оплату."""
    order_id = await s.order(client, paid=True)
    s.clock.advance(timedelta(minutes=2))

    await s.checks.check_fresh()

    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED
    assert len(s.messages.granted) == 1


async def test_payment_not_recorded_is_picked_up_by_background_check(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    """Комментарий владельца к задаче 13: оплата не записалась в базу — проверка подхватит."""
    s = build_payment_setup(
        session_factory,
        clock,
        mask,
        crediting_sessions=flaky_commits(session_factory, failures=3),
    )
    order_id = await s.order(client, paid=True)
    with pytest.raises(OperationalError):
        await s.checks.handle_notification(await notice_of(s, order_id), SOURCE)
    assert (await s.load(order_id)).status is OrderStatus.CREATED

    s.clock.advance(timedelta(minutes=2))
    await s.checks.check_fresh()

    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED


async def test_expired_payment_marks_order_canceled(s: PaymentSetup, client: Client) -> None:
    order_id = await s.order(client)
    s.clock.advance(PAYMENT_LINK_LIFETIME)

    await s.checks.check_fresh()

    assert (await s.load(order_id)).status is OrderStatus.CANCELED


async def test_stale_check_covers_canceled_orders_up_to_three_days(
    s: PaymentSetup, client: Client
) -> None:
    """Медленный темп: 2–72 часа, включая «отменён»; быстрый их не трогает."""
    order_id = await s.order(client, paid=True)
    await s.set_status(order_id, OrderStatus.CANCELED)
    s.clock.advance(FRESH_ORDER_AGE + timedelta(minutes=1))

    await s.checks.check_fresh()
    assert s.payments.asked == []

    await s.checks.check_stale()
    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED


async def test_orders_older_than_three_days_are_not_checked(
    s: PaymentSetup, client: Client
) -> None:
    await s.order(client, paid=True)
    s.clock.advance(STALE_ORDER_AGE)

    await s.checks.check_fresh()
    await s.checks.check_stale()

    assert s.payments.asked == []


async def test_fresh_check_skips_credited_and_canceled_orders(
    s: PaymentSetup, client: Client
) -> None:
    await s.order(client, paid=True, status=OrderStatus.ACCESS_GRANTED)
    await s.order(client, status=OrderStatus.CANCELED)

    await s.checks.check_fresh()

    assert s.payments.asked == []


async def test_provider_down_in_background_raises_one_alert(
    s: PaymentSetup, client: Client
) -> None:
    await s.order(client)
    await s.order(client)
    s.payments.fail_next(PaymentUnavailable("таймаут"))

    await s.checks.check_fresh()

    assert len(s.payments.asked) == 1  # первый же запрос упал — второй заказ в следующий раз
    [alert] = s.alerts.sent
    assert s.admin_texts.get("alert.payment_checks_unavailable.title") in alert.text
    assert "PaymentChecks._check_all" in alert.text


async def test_background_checks_skip_when_payments_off(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    """`PAYMENTS_MODE=off`: платёжки нет — фоновые проверки ничего не делают и не падают."""
    s = build_payment_setup(session_factory, clock, mask, payments_off=True)

    await s.checks.check_fresh()
    await s.checks.check_stale()

    assert await s.checks.find_paid(client.id) is None
    assert s.alerts.sent == []


# --- «🔄 Проверить оплату» ---


async def test_check_button_credits_own_paid_order(s: PaymentSetup, client: Client) -> None:
    order_id = await s.order(client, paid=True)

    assert await s.checks.check_order(client.id, order_id) is CheckOutcome.CREDITED
    assert await s.checks.check_order(client.id, order_id) is CheckOutcome.ALREADY_CREDITED
    assert len(s.payments.asked) == 1


async def test_check_button_of_unpaid_and_expired_orders(s: PaymentSetup, client: Client) -> None:
    order_id = await s.order(client)

    assert await s.checks.check_order(client.id, order_id) is CheckOutcome.NOT_PAID

    s.clock.advance(PAYMENT_LINK_LIFETIME)
    assert await s.checks.check_order(client.id, order_id) is CheckOutcome.CANCELED


async def test_check_button_with_other_clients_order_finds_nothing(
    s: PaymentSetup, client: Client, other: Client
) -> None:
    """Б21: подделанная кнопка с чужим заказом — заказ ищется только у нажавшего."""
    order_id = await s.order(other, paid=True)

    assert await s.checks.check_order(client.id, order_id) is CheckOutcome.NOT_FOUND
    assert await s.checks.check_order(client.id, order_id + 100) is CheckOutcome.NOT_FOUND
    assert s.payments.asked == []
    assert (await s.load(order_id)).status is OrderStatus.CREATED
