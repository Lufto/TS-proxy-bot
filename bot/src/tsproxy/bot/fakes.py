"""Подделка Telegram для тестов: бот без сети, который запоминает все запросы.

`Bot(token, session=FakeTelegramSession())` ведёт себя как настоящий, но ничего не шлёт:
запросы копятся в `session.requests`, ответы собираются из самого запроса. Ошибку Telegram
на следующий запрос нужного вида задаёт `fail_next(SendMessage, TelegramForbiddenError, "…")`,
медленный Telegram — `hold(SendChatAction)`: запросы этого вида ждут, пока тест не отпустит.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, cast

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import (
    CopyMessage,
    CreateForumTopic,
    EditMessageText,
    GetMe,
    GetUpdates,
    SendInvoice,
    SendMessage,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, ForumTopic, Message, MessageId, User

FAKE_BOT_USERNAME = "ts_proxy_test_bot"
FIRST_MESSAGE_ID = 1000
FAKE_TOPIC_ICON_COLOR = 0x6FB9F0  # один из цветов тем, которые разрешает Telegram
# Пауза «долгого опроса»: без неё polling на подделке крутится, не отпуская остальных
FAKE_POLL_DELAY_SECONDS = 0.05


class FakeTelegramSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.closed = False
        self._failures: dict[
            type[TelegramMethod[Any]], tuple[type[TelegramAPIError], str, dict[str, Any]]
        ] = {}
        self._next_message_id = FIRST_MESSAGE_ID
        self._holds: dict[type[TelegramMethod[Any]], asyncio.Event] = {}

    # --- Помощники для тестов ---

    def fail_next(
        self,
        method_type: type[TelegramMethod[Any]],
        error_type: type[TelegramAPIError],
        message: str,
        **extra: Any,
    ) -> None:
        """Следующий запрос вида `method_type` закончится ошибкой Telegram.

        `extra` — дополнительные поля ошибки, например `retry_after=5` у `TelegramRetryAfter`.
        """
        self._failures[method_type] = (error_type, message, extra)

    def hold(self, method_type: type[TelegramMethod[Any]]) -> asyncio.Event:
        """Запросы вида `method_type` повиснут (Telegram думает), пока тест не вызовет
        `set()` у возвращённого события."""
        release = asyncio.Event()
        self._holds[method_type] = release
        return release

    @property
    def last_message_id(self) -> int:
        """Номер последнего сообщения, которое прислал бот (Telegram нумерует подряд)."""
        return self._next_message_id

    def sent[M: TelegramMethod[Any]](self, method_type: type[M]) -> list[M]:
        """Запросы одного вида, по порядку."""
        return [r for r in self.requests if isinstance(r, method_type)]

    # --- BaseSession ---

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109  # так у BaseSession
    ) -> TelegramType:
        self.requests.append(method)
        if isinstance(method, GetUpdates):
            await asyncio.sleep(FAKE_POLL_DELAY_SECONDS)
        release = self._holds.get(type(method))
        if release is not None:
            await release.wait()
        failure = self._failures.pop(type(method), None)
        if failure is not None:
            error_type, message, extra = failure
            raise error_type(method=method, message=message, **extra)
        return cast(TelegramType, self._result(bot, method))

    async def close(self) -> None:
        self.closed = True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109  # так у BaseSession
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        raise NotImplementedError("Подделка Telegram не скачивает файлы")
        yield b""  # pragma: no cover  # делает функцию генератором, как у настоящей сессии

    # --- Ответы ---

    def _message(self, chat_id: int | str, message_id: int, text: str) -> Message:
        chat_type = ChatType.PRIVATE if int(chat_id) > 0 else ChatType.SUPERGROUP
        return Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=int(chat_id), type=chat_type),
            text=text,
        )

    def _result(self, bot: Bot, method: TelegramMethod[Any]) -> object:
        if isinstance(method, SendMessage):
            self._next_message_id += 1
            return self._message(method.chat_id, self._next_message_id, method.text)
        if isinstance(method, SendInvoice):
            self._next_message_id += 1
            return self._message(method.chat_id, self._next_message_id, method.title)
        if isinstance(method, CopyMessage):
            self._next_message_id += 1
            return MessageId(message_id=self._next_message_id)
        if isinstance(method, CreateForumTopic):
            # номер темы в Telegram — номер служебного сообщения «тема создана»
            self._next_message_id += 1
            return ForumTopic(
                message_thread_id=self._next_message_id,
                name=method.name,
                icon_color=FAKE_TOPIC_ICON_COLOR,
            )
        if isinstance(method, EditMessageText):
            if method.chat_id is None or method.message_id is None:
                return True
            return self._message(method.chat_id, method.message_id, method.text)
        if isinstance(method, GetUpdates):
            return []  # новых апдейтов нет (для polling)
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="TS Proxy", username=FAKE_BOT_USERNAME)
        # answerCallbackQuery, leaveChat, setWebhook, deleteWebhook, setMyCommands, …
        return True
