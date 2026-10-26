"""`/health` для внешнего сторожа UptimeRobot (задача 18 в `vpn-bot-rework`, задача 22).

Наружу — только «ok» (200) или «проблема» (503): ни имён задач, ни ошибок, ни версий.
Подробности пишет в лог сама проверка (`services/health.py`). HEAD тоже принимается:
UptimeRobot по умолчанию проверяет им.
"""

from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, status
from fastapi.responses import PlainTextResponse

HEALTH_PATH = "/health"
HEALTHY_BODY = "ok"
UNHEALTHY_BODY = "проблема"
# Ответ не кэшируют посредники: иначе сторож увидит старое «ok»
NO_CACHE_HEADERS = {"Cache-Control": "no-store"}


class HealthProbe(Protocol):
    async def is_healthy(self) -> bool: ...


@dataclass(frozen=True)
class HealthEndpoint:
    probe: HealthProbe

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route(
            HEALTH_PATH, self.health, methods=["GET", "HEAD"], include_in_schema=False
        )
        return router

    async def health(self) -> PlainTextResponse:
        if await self.probe.is_healthy():
            return PlainTextResponse(HEALTHY_BODY, headers=NO_CACHE_HEADERS)
        return PlainTextResponse(
            UNHEALTHY_BODY,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers=NO_CACHE_HEADERS,
        )
