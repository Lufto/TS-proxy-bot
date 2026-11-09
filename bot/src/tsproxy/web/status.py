"""`/status` для сайта ts-proxy.com: какую плашку о неполадках показать (задача сайта 13).

Отвечает `{"banner": "none" | "maintenance" | "proxy_issues"}`. Если бот не ответил,
сайт сам показывает «бот не отвечает». Ответ разрешено читать с любого сайта и не кэшировать:
тайн в нём нет, а старая плашка у посетителя хуже лишнего запроса. HEAD тоже принимается.
"""

from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from tsproxy.domain.site_banner import SiteBanner

STATUS_PATH = "/status"
RESPONSE_HEADERS = {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"}


class BannerSource(Protocol):
    async def current(self) -> SiteBanner: ...


@dataclass(frozen=True)
class StatusEndpoint:
    banner: BannerSource

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route(
            STATUS_PATH, self.status, methods=["GET", "HEAD"], include_in_schema=False
        )
        return router

    async def status(self) -> JSONResponse:
        banner = await self.banner.current()
        return JSONResponse({"banner": banner.value}, headers=RESPONSE_HEADERS)
