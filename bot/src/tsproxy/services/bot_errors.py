"""«Ошибка в боте» (bot-alerts, задача 04): упал обработчик апдейта или фоновая задача.

Тревога называет тип и сообщение ошибки, последнее место в нашем коде из трассировки
(`модуль.функция:строка`) и что обрабатывалось (`action` — готовый текст от вызывающего:
кнопка, команда, фоновая задача). Место берётся из трассировки, а не передаётся явно: ошибку
ловит общий обработчик, а не то место, где она случилась. Нашего кода в трассировке нет
(ошибка внутри библиотеки) — вместо места `alert.bot_error.no_place`.

Склейка — по типу ошибки: тысяча одинаковых `KeyError` — одна тревога и сводка повторов.
"""

from tsproxy.content.loader import Html, Texts
from tsproxy.services.alerts import Alerter

BOT_ERROR_ALERT = "bot_error"
OUR_PACKAGE = "tsproxy"


def code_place(error: BaseException) -> str | None:
    """Последний кадр трассировки из нашего кода: `tsproxy.bot.connect._account_screen:63`."""
    place = None
    frame = error.__traceback__
    while frame is not None:
        module = str(frame.tb_frame.f_globals.get("__name__", ""))
        if module == OUR_PACKAGE or module.startswith(f"{OUR_PACKAGE}."):
            place = f"{module}.{frame.tb_frame.f_code.co_qualname}:{frame.tb_lineno}"
        frame = frame.tb_next
    return place


async def alert_bot_error(alerter: Alerter, texts: Texts, error: Exception, action: str) -> None:
    """Тревога «Ошибка в боте». `action` — что обрабатывалось, готовым текстом из `Texts.get`."""
    place = code_place(error) or texts.get("alert.bot_error.no_place")
    await alerter.alert(
        BOT_ERROR_ALERT,
        source=place,
        error=error,
        glue=f"{BOT_ERROR_ALERT}:{type(error).__name__}",
        action=Html(action),
    )
