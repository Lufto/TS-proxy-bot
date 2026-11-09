"""«🚀 Подключиться»: ссылка подписки только своя, без аккаунта — предложение пробного."""

from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import InlineKeyboardButton
from bot_updates import (
    ADMIN_CHAT_ID,
    ALERTS_THREAD_ID,
    CLIENT_ID,
    NOW,
    OTHER_CLIENT_ID,
    callback,
    user,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.connect import deliver_pending_connects
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.models import Client
from tsproxy.domain.dates import format_moscow
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    AccountStatus,
    ProxyAccount,
    RemnawaveUnavailable,
)
from tsproxy.services.fakes import FakeAlertSender, FakeClock

CONNECT = MenuCallback(screen=ScreenId.CONNECT).pack()
EXPIRE_AT = NOW + timedelta(days=30)


async def create_account(remnawave: FakeRemnawave, telegram_id: int = CLIENT_ID) -> ProxyAccount:
    return await remnawave.get_or_create_account(
        telegram_id, expire_at=EXPIRE_AT, device_limit=3, traffic_limit_bytes=0
    )


def buttons(edited: EditMessageText) -> list[InlineKeyboardButton]:
    assert edited.reply_markup is not None
    return [b for row in edited.reply_markup.inline_keyboard for b in row]  # type: ignore[union-attr]


async def test_connect_opens_own_subscription_page(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    account = await create_account(remnawave)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("connect.active", expire_at=format_moscow(EXPIRE_AT))
    url_buttons = [b for b in buttons(edited) if b.url is not None]
    copy_buttons = [b for b in buttons(edited) if b.copy_text is not None]
    assert [b.url for b in url_buttons] == [account.subscription_url]
    assert [b.copy_text.text for b in copy_buttons] == [account.subscription_url]  # type: ignore[union-attr]
    assert copy_buttons[0].text == content.texts.get("buttons.copy_link")


async def test_connect_without_account_offers_trial(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("connect.no_account")
    texts = [b.text for b in buttons(edited)]
    assert content.texts.get("buttons.trial") in texts
    assert all(b.url is None and b.copy_text is None for b in buttons(edited))


async def test_trial_marked_but_never_delivered_is_offered_again(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    """Пробный отмечен, а аккаунта нет — панель не ответила при выдаче. Доступа клиент
    не получал, поэтому кнопка пробного остаётся: повторное нажатие доделает выдачу."""
    async with session_factory() as session:
        session.add(Client(telegram_id=CLIENT_ID, first_seen_at=NOW, trial_granted_at=NOW))
        await session.commit()

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("connect.no_account")
    assert content.texts.get("buttons.trial") in [b.text for b in buttons(edited)]


async def test_button_of_another_client_shows_only_own_data(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, remnawave: FakeRemnawave
) -> None:
    """Кнопка, нажатая под сообщением другого клиента (или подделанная), не даёт чужую ссылку:
    клиент определяется только по `from_user.id`."""
    victim = await create_account(remnawave, CLIENT_ID)

    update = callback(user(OTHER_CLIENT_ID), CONNECT, chat_id=None)
    await dispatcher.feed_update(bot, update)

    for request in telegram.requests:
        assert victim.subscription_url not in request.model_dump_json(fallback=repr)


async def test_expired_subscription_suggests_tariffs(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    content: Content,
) -> None:
    await create_account(remnawave)
    remnawave.set_status(CLIENT_ID, AccountStatus.EXPIRED)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("connect.inactive", expire_at=format_moscow(EXPIRE_AT))
    assert content.texts.get("buttons.tariffs") in [b.text for b in buttons(edited)]


async def test_panel_unavailable_apologizes_and_alerts(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    content: Content,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("timeout"))

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("connect.pending")
    [alert] = alert_sender.sent
    assert (alert.chat_id, alert.thread_id) == (ADMIN_CHAT_ID, ALERTS_THREAD_ID)
    assert content.admin_texts.get("alerts.remnawave_unavailable") in alert.text


async def test_link_is_sent_once_when_panel_answers_again(
    dispatcher: Dispatcher,
    bot: Bot,
    ctx: BotContext,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    clock: FakeClock,
    content: Content,
) -> None:
    """Панель не ответила на «Подключиться» — бот сам присылает ссылку, когда она ожила."""
    account = await create_account(remnawave)
    remnawave.fail_next(RemnawaveUnavailable("timeout"))
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))
    telegram.requests.clear()
    clock.advance(timedelta(minutes=1))

    await deliver_pending_connects(ctx)
    await deliver_pending_connects(ctx)  # второй проход — уже ничего

    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == CLIENT_ID
    assert sent.text == content.texts.get("connect.active", expire_at=format_moscow(EXPIRE_AT))
    assert account.subscription_url in [
        b.url
        for row in sent.reply_markup.inline_keyboard
        for b in row  # type: ignore[union-attr]
    ]


async def test_link_waits_while_panel_is_still_down(
    dispatcher: Dispatcher,
    bot: Bot,
    ctx: BotContext,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    await create_account(remnawave)
    remnawave.fail_next(RemnawaveUnavailable("timeout"))
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))
    telegram.requests.clear()

    remnawave.fail_next(RemnawaveUnavailable("timeout"))
    await deliver_pending_connects(ctx)
    assert telegram.sent(SendMessage) == []

    await deliver_pending_connects(ctx)
    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == CLIENT_ID


async def test_client_not_waiting_after_a_day(
    dispatcher: Dispatcher,
    bot: Bot,
    ctx: BotContext,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    """Сутки панель лежала — ссылку уже не шлём: клиент давно мог открыть её сам."""
    await create_account(remnawave)
    remnawave.fail_next(RemnawaveUnavailable("timeout"))
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))
    telegram.requests.clear()
    clock.advance(timedelta(days=1, minutes=1))

    await deliver_pending_connects(ctx)

    assert telegram.sent(SendMessage) == []


async def test_client_who_opened_link_himself_gets_nothing_extra(
    dispatcher: Dispatcher,
    bot: Bot,
    ctx: BotContext,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> None:
    await create_account(remnawave)
    remnawave.fail_next(RemnawaveUnavailable("timeout"))
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))
    clock.advance(timedelta(minutes=1))
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), CONNECT))  # панель уже ответила
    telegram.requests.clear()

    await deliver_pending_connects(ctx)

    assert telegram.sent(SendMessage) == []
