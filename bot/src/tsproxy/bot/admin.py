"""Кто администратор и где работает админка (аудит R19, §4.3).

Действия администратора — только от `ADMIN_IDS` и только в группе администраторов
(`ADMIN_CHAT_ID`). Проверка стоит на обработчике (фильтр `AdminFilter`), а не «кнопку
показали только администратору»: кнопку может прислать кто угодно.
"""

from dataclasses import dataclass

from aiogram.enums import ChatType
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message


@dataclass(frozen=True)
class AdminAccess:
    admin_ids: frozenset[int]
    admin_chat_id: int

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def is_admin_chat(self, chat_id: int) -> bool:
        return chat_id == self.admin_chat_id

    def allows(self, user_id: int, chat_id: int) -> bool:
        """Действие разрешено: администратор и в группе администраторов."""
        return self.is_admin(user_id) and self.is_admin_chat(chat_id)


class AdminFilter(Filter):
    """Пропускает только сообщения и нажатия администраторов в группе администраторов.

    Подключается к роутеру админки: `router.message.filter(AdminFilter(access))`.
    """

    def __init__(self, access: AdminAccess) -> None:
        self._access = access

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if event.from_user is None:
            return False  # сообщение от имени канала или анонимного администратора
        if isinstance(event, CallbackQuery):
            if event.message is None:
                return False
            chat_id = event.message.chat.id
        else:
            chat_id = event.chat.id
        return self._access.allows(event.from_user.id, chat_id)


class ForeignChatFilter(Filter):
    """Чат — не личный и не группа администраторов: из такого бот выходит."""

    def __init__(self, access: AdminAccess) -> None:
        self._access = access

    async def __call__(self, event: Message | ChatMemberUpdated) -> bool:
        chat = event.chat
        return chat.type != ChatType.PRIVATE and not self._access.is_admin_chat(chat.id)
