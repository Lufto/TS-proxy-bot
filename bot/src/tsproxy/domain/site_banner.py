"""Плашка о неполадках на сайте ts-proxy.com (задачи сайта 13–15, решение задачи карты 02).

«Бот не отвечает» сайт определяет сам — по тому, что `/status` молчит. Здесь — плашки,
которые ставит администратор. Значения уходят на сайт как есть: менять их — менять и сайт
(`web/src/shared/lib/status`).
"""

from enum import StrEnum


class SiteBanner(StrEnum):
    NONE = "none"  # плашки нет
    MAINTENANCE = "maintenance"  # технические работы
    PROXY_ISSUES = "proxy_issues"  # перебои у прокси
