"""Подделки для тестов сервисов: управляемые часы, отправка тревог без Telegram,
выключатель пробных без журнала, проверка прошлых оплат без платёжки."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tsproxy.services.alerts import AlertButton


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
    button: AlertButton | None = None


@dataclass
class FakeAlertSender:
    """Запоминает отправленные сообщения. С `error` вместо отправки бросает эту ошибку."""

    sent: list[SentMessage] = field(default_factory=list)
    error: Exception | None = None

    async def send(
        self, chat_id: int, thread_id: int | None, text: str, button: AlertButton | None = None
    ) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(SentMessage(chat_id, thread_id, text, button))


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
class FakeEarlierPayments:
    """Проверка прошлых оплат перед новой ссылкой (задача 14) без платёжки.

    `paid_order_id` ставит тест: «нашлась оплата этого заказа»; `checked` — чьи заказы проверяли.
    """

    paid_order_id: int | None = None
    checked: list[int] = field(default_factory=list)

    async def find_paid(self, client_id: int) -> int | None:
        self.checked.append(client_id)
        return self.paid_order_id
