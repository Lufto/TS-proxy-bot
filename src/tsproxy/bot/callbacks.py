"""Данные кнопок (`callback_data`) — только через типизированные фабрики aiogram.

Кнопку присылает приложение клиента, и модифицированное приложение пришлёт что угодно
(аудит §4.3). Поэтому в данных кнопок нет ничего, что даёт доступ: только «какой экран
открыть». Чей это клиент, бот всегда определяет по `from_user.id` нажавшего.
Кривые данные отбрасывает pydantic: такой кнопке не подходит ни один обработчик.
"""

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class ScreenId(StrEnum):
    """Экраны меню, которые меняются в одном сообщении."""

    MAIN = "main"
    CONNECT = "connect"  # «🚀 Подключиться»
    TRIAL = "trial"  # «🎁 Попробовать бесплатно» — задача 07
    TARIFFS = "tariffs"  # «💳 Тарифы» — задача 12
    PROFILE = "profile"  # «👤 Мой профиль» — задача 08
    INSTRUCTIONS = "instructions"
    SUPPORT = "support"  # «💬 Поддержка» — задачи 16–17
    ABOUT = "about"


class Platform(StrEnum):
    """Платформы в «📖 Инструкциях»."""

    IOS = "ios"
    ANDROID = "android"
    WINDOWS = "windows"
    MACOS = "macos"


class MenuCallback(CallbackData, prefix="menu"):
    screen: ScreenId


class InstructionsCallback(CallbackData, prefix="howto"):
    platform: Platform
