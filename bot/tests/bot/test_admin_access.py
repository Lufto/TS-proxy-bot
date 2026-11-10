"""Команды админки из общего диспетчера — только от `ADMIN_IDS` и только в группе
администраторов (аудит перед оплатой, задача 04).

Два случая на каждую команду: обычный клиент в группе администраторов и администратор
в личном чате с ботом. Остальные части админки проверены в своих файлах
(`test_*_admin.py`, кнопки темы — `test_support_topic_actions.py`).
"""

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Chat
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, CLIENT_ID, OTHER_CLIENT_ID, message, user
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.db.models import Event
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountNotFound

COMMANDS = [f"/client {CLIENT_ID}", f"/give {CLIENT_ID} 30", "/banner", "/trials"]

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup")
STRANGER_IN_ADMIN_GROUP = "stranger_in_admin_group"
ADMIN_IN_PRIVATE_CHAT = "admin_in_private_chat"


async def count_events(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Event)) or 0


def reached_admin_group(telegram: FakeTelegramSession) -> bool:
    requests = [*telegram.sent(SendMessage), *telegram.sent(EditMessageText)]
    return any(request.chat_id == ADMIN_CHAT_ID for request in requests)


@pytest.mark.parametrize("who", [STRANGER_IN_ADMIN_GROUP, ADMIN_IN_PRIVATE_CHAT])
@pytest.mark.parametrize("command", COMMANDS)
async def test_admin_command_does_nothing_for_wrong_person_or_chat(
    who: str,
    command: str,
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if who == STRANGER_IN_ADMIN_GROUP:
        update = message(user(OTHER_CLIENT_ID), command, chat=ADMIN_GROUP)
    else:
        update = message(user(ADMIN_ID), command)

    await dispatcher.feed_update(bot, update)

    assert not reached_admin_group(telegram)
    assert await count_events(session_factory) == 0
    with pytest.raises(AccountNotFound):
        await remnawave.get_account(CLIENT_ID)


@pytest.mark.parametrize("command", COMMANDS)
async def test_admin_in_admin_group_does_get_answer(
    command: str, dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    """Проверка самого теста выше: правильный человек в правильном чате ответ получает."""
    await dispatcher.feed_update(bot, message(user(ADMIN_ID), command, chat=ADMIN_GROUP))

    assert reached_admin_group(telegram)
