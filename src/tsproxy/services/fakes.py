"""Подделки для тестов сервисов: управляемые часы, отправка тревог без Telegram,
выключатель пробных без журнала, удаление тем группы."""

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


@dataclass
class FakeTrialGate:
    """Выключатель пробных без журнала: `enabled` ставит тест, `new_trials` — счётчик нажатий."""

    is_enabled: bool = True
    new_trials: int = 0

    async def enabled(self) -> bool:
        return self.is_enabled

    async def note_new_trial(self) -> None:
        self.new_trials += 1


@dataclass
class FakeTopicRemover:
    """Удаление тем группы без Telegram: запоминает удалённые темы.

    `fail_on` — на этой теме бросить `error` (как Telegram без права «Управление темами»).
    """

    removed: list[int] = field(default_factory=list)
    fail_on: int | None = None
    error: Exception | None = None

    async def remove(self, topic_id: int) -> None:
        if topic_id == self.fail_on and self.error is not None:
            raise self.error
        self.removed.append(topic_id)
