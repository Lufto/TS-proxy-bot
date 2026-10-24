"""Апдейты Telegram для тестов — такие, какие присылает Telegram, и общие константы."""

from datetime import UTC, datetime
from itertools import count

from aiogram.enums import ChatType
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberBanned,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    MessageEntity,
    Update,
    User,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
DATE = NOW
BOT_TOKEN = "42:TESTtokenTESTtokenTESTtoken"
BOT_ID = 42
ADMIN_ID = 900
ADMIN_CHAT_ID = -100500
ALERTS_THREAD_ID = 7
CLIENT_ID = 111
OTHER_CLIENT_ID = 222
MENU_MESSAGE_ID = 77
_update_ids = count(1)


def user(user_id: int, *, first_name: str = "Иван", username: str | None = "ivan") -> User:
    return User(id=user_id, is_bot=False, first_name=first_name, username=username)


def private_chat(user_id: int) -> Chat:
    return Chat(id=user_id, type=ChatType.PRIVATE)


def message(from_user: User, text: str, *, chat: Chat | None = None, message_id: int = 1) -> Update:
    entities = None
    if text.startswith("/"):
        command_length = len(text.split(maxsplit=1)[0])
        entities = [MessageEntity(type="bot_command", offset=0, length=command_length)]
    return Update(
        update_id=next(_update_ids),
        message=Message(
            message_id=message_id,
            date=DATE,
            chat=chat or private_chat(from_user.id),
            from_user=from_user,
            text=text,
            entities=entities,
        ),
    )


def callback(
    from_user: User, data: str, *, chat_id: int | None = None, message_id: int = MENU_MESSAGE_ID
) -> Update:
    """Нажатие кнопки под сообщением бота `message_id` в чате `chat_id` (по умолчанию — личном)."""
    chat = private_chat(from_user.id) if chat_id is None else Chat(id=chat_id, type="supergroup")
    return Update(
        update_id=next(_update_ids),
        callback_query=CallbackQuery(
            id=str(next(_update_ids)),
            from_user=from_user,
            chat_instance="ci",
            data=data,
            message=Message(message_id=message_id, date=DATE, chat=chat, text="меню"),
        ),
    )


def bot_status_changed(chat: Chat, from_user: User, *, bot_id: int, present: bool) -> Update:
    """Бота добавили в чат (`present=True`) или убрали/заблокировали (`False`)."""
    bot_user = User(id=bot_id, is_bot=True, first_name="TS Proxy")
    old = ChatMemberBanned(user=bot_user, until_date=DATE)
    new = ChatMemberMember(user=bot_user)
    if not present:
        old, new = new, old
    return Update(
        update_id=next(_update_ids),
        my_chat_member=ChatMemberUpdated(
            chat=chat,
            from_user=from_user,
            date=DATE,
            old_chat_member=old,
            new_chat_member=new,
        ),
    )
