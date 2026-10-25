"""Сообщения в тему группы администраторов, кроме тревог (у тех своя склейка — `alerts.py`).

Например, «💰 Оплаты»: каждая оплата — одно сообщение (спецификация, админка п. 1).
"""

import logging

from tsproxy.services.alerts import AlertSender

logger = logging.getLogger(__name__)


class AdminTopic:
    """Тема группы администраторов. `thread_id=None` — общий чат группы."""

    def __init__(self, sender: AlertSender, chat_id: int, thread_id: int | None) -> None:
        self._sender = sender
        self._chat_id = chat_id
        self._thread_id = thread_id

    async def post(self, text: str) -> None:
        """Отправить сообщение (HTML). Сбой отправки не роняет вызывающий код — только лог."""
        try:
            await self._sender.send(self._chat_id, self._thread_id, text)
        # Ловим всё: сообщение администраторам не должно ронять оплату, а ошибки Telegram
        # и сети бывают разных типов. Ошибку не прячем — пишем в лог с трассировкой.
        except Exception:
            logger.exception("Не удалось отправить сообщение в тему %s", self._thread_id)
