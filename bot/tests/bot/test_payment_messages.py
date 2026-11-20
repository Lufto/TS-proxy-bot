"""Сообщения клиенту об оплате (задача 13): «✅ Оплачено, подписка до …» и «доступ подключается»."""

from datetime import timedelta
from decimal import Decimal

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardMarkup
from bot_updates import CLIENT_ID, NOW
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import KEPT_TEXT_MARK
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.payments import PaymentScreens, TelegramPaymentMessages
from tsproxy.bot.screens import Screens
from tsproxy.content.dates import client_time
from tsproxy.content.loader import REQUIRED_TEXTS, Content, Texts
from tsproxy.db.models import Client
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import ProxyAccount
from tsproxy.services.crediting import GrantedAccess

PRICE = Decimal("150")


def messages(ctx: BotContext) -> TelegramPaymentMessages:
    return TelegramPaymentMessages(ctx.delivery, PaymentScreens(ctx.texts, ctx.screens))


async def paid_account(remnawave: FakeRemnawave) -> ProxyAccount:
    return await remnawave.get_or_create_account(
        CLIENT_ID, expire_at=NOW + timedelta(days=30), device_limit=3, traffic_limit_bytes=0
    )


def granted(account: ProxyAccount, paid: Decimal = PRICE) -> GrantedAccess:
    return GrantedAccess(CLIENT_ID, 1, account, PRICE, paid)


def button_texts(request: SendMessage) -> list[str]:
    assert request.reply_markup is not None
    return [b.text for row in request.reply_markup.inline_keyboard for b in row]  # type: ignore[union-attr]


async def test_paid_message_shows_date_and_subscription_link(
    ctx: BotContext, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    account = await paid_account(remnawave)

    await messages(ctx).access_granted(granted(account))

    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == CLIENT_ID
    assert sent.text.startswith(
        '<b>✅ Оплачено, подписка до <tg-time unix="1795435200" format="Dt">'
        "23.11.2026, 15:00 МСК</tg-time></b>"
    )
    assert "сбоя" not in sent.text
    assert ctx.texts.get("buttons.connect") in button_texts(sent)
    assert ctx.texts.get("buttons.instructions") in button_texts(sent)


async def test_paid_message_keeps_link_in_text(
    ctx: BotContext, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    """Новое меню гасит кнопки сообщения «Оплачено» — ссылка остаётся в тексте (первый
    клиент, 2026-11-20: открыл профиль, и ссылки не осталось нигде)."""
    account = await paid_account(remnawave)

    await messages(ctx).access_granted(granted(account))
    await messages(ctx).access_granted(granted(account, paid=Decimal("75")))

    for sent in telegram.sent(SendMessage):
        assert f'<a href="{account.subscription_url}">' in sent.text


async def test_other_amount_comes_with_apology(
    ctx: BotContext, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    account = await paid_account(remnawave)

    await messages(ctx).access_granted(granted(account, paid=Decimal("75")))

    [sent] = telegram.sent(SendMessage)
    assert "✅ Оплачено" in sent.text
    assert "75 ₽ вместо 150 ₽" in sent.text


async def test_pending_message_promises_link_later(
    ctx: BotContext, telegram: FakeTelegramSession
) -> None:
    await messages(ctx).access_pending(CLIENT_ID)

    [sent] = telegram.sent(SendMessage)
    assert sent.text == ctx.texts.get("payment.pending")


async def test_delayed_message_says_payment_is_safe(
    ctx: BotContext, telegram: FakeTelegramSession
) -> None:
    """Доступ не выдан за 30 минут — второе сообщение (задача 14, решение владельца п. 3)."""
    await messages(ctx).access_delayed(CLIENT_ID)

    [sent] = telegram.sent(SendMessage)
    assert sent.text == ctx.texts.get("payment.delayed")


async def test_double_payment_message_points_to_support(
    ctx: BotContext, telegram: FakeTelegramSession
) -> None:
    """Две оплаты подряд: дни сложены, случайную — через поддержку, кнопки возврата нет."""
    await messages(ctx).possible_double_payment(CLIENT_ID)

    [sent] = telegram.sent(SendMessage)
    assert "две оплаты" in sent.text
    assert isinstance(sent.reply_markup, InlineKeyboardMarkup)
    data = [b.callback_data for row in sent.reply_markup.inline_keyboard for b in row]
    # сообщение об оплате не удаляется при нажатии: кнопки с меткой сохраняемого текста
    assert data == [KEPT_TEXT_MARK + MenuCallback(screen=ScreenId.SUPPORT).pack()]


async def test_client_who_blocked_bot_is_marked_and_nothing_breaks(
    ctx: BotContext,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=CLIENT_ID, first_seen_at=NOW))
        await session.commit()
    telegram.fail_next(SendMessage, TelegramForbiddenError, "bot was blocked by the user")

    await messages(ctx).access_pending(CLIENT_ID)

    client = await ctx.clients.find(CLIENT_ID)
    assert client is not None and client.bot_blocked_at is not None


async def test_telegram_failure_does_not_break_crediting(
    ctx: BotContext, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    account = await paid_account(remnawave)
    telegram.fail_next(SendMessage, TelegramNetworkError, "timeout")

    await messages(ctx).access_granted(granted(account))  # не бросает


async def test_payment_screens_use_only_required_texts(
    content: Content, remnawave: FakeRemnawave
) -> None:
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    screens = PaymentScreens(texts, Screens(texts))
    account = await paid_account(remnawave)

    screens.paid(granted(account))
    screens.paid(granted(account, paid=Decimal("75")))
    screens.pending()
    screens.delayed()
    screens.double()


async def test_dispute_message_points_to_support(
    ctx: BotContext, telegram: FakeTelegramSession
) -> None:
    """Задача 15: спор через банк — дни оплаты сняты, вопросы — в поддержку."""
    await messages(ctx).payment_disputed(CLIENT_ID, NOW, access_ended=False)
    await messages(ctx).payment_disputed(CLIENT_ID, None, access_ended=True)
    await messages(ctx).payment_disputed(CLIENT_ID, None, access_ended=False)

    shortened, ended, pending = telegram.sent(SendMessage)
    assert shortened.text == ctx.texts.get("payment.disputed", expire_at=client_time(NOW))
    assert ended.text == ctx.texts.get("payment.disputed_ended")
    assert pending.text == ctx.texts.get("payment.disputed_pending")
    for sent in (shortened, ended, pending):
        assert sent.chat_id == CLIENT_ID
        assert button_texts(sent) == [ctx.texts.get("buttons.support")]
