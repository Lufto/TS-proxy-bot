"""Дата в сообщении клиенту — одно место форматирования (задача 22 «Время по часам клиента»).

Telegram (Bot API 9.5+) сам показывает дату из тега `<tg-time>` по часам клиента и на его
языке. Внутри тега — запасной текст по Москве «10.12.2026, 18:00 МСК»: его видят старые
версии Telegram. Тексты администратора остаются по Москве (`format_moscow`).
"""

import html
from datetime import datetime

from aiogram.utils.text_decorations import html_decoration

from tsproxy.content.loader import Html
from tsproxy.domain.dates import format_moscow

# Длинная дата и короткое время: «10 декабря 2026 г., 18:00» на языке клиента
CLIENT_TIME_FORMAT = "Dt"


def client_time(moment: datetime) -> Html:
    """`<tg-time>` с запасным текстом по Москве; `Texts.get` его не экранирует."""
    fallback = html.escape(format_moscow(moment))
    return Html(
        html_decoration.date_time(
            value=fallback, unix_time=int(moment.timestamp()), date_time_format=CLIENT_TIME_FORMAT
        )
    )
