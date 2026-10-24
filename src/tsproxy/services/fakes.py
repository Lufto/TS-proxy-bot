"""Подделки для тестов сервисов: управляемые часы и отправка тревог без Telegram."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta


class FakeClock:
    """Часы, которые стоят на месте, пока тест не сдвинет их сам."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass(frozen=True)
class SentMessage:
    chat_id: int
    thread_id: int | None
    text: str


@dataclass
class FakeAlertSender:
    """Запоминает отправленные сообщения. С `error` вместо отправки бросает эту ошибку."""

    sent: list[SentMessage] = field(default_factory=list)
    error: Exception | None = None

    async def send(self, chat_id: int, thread_id: int | None, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(SentMessage(chat_id, thread_id, text))
