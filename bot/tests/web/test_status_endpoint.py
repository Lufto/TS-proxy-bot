"""`/status` говорит сайту, какую плашку показать (задача сайта 13)."""

from dataclasses import dataclass

import httpx

from tsproxy.domain.site_banner import SiteBanner
from tsproxy.web.app import create_web_app
from tsproxy.web.status import STATUS_PATH, StatusEndpoint


@dataclass
class StubBanner:
    banner: SiteBanner

    async def current(self) -> SiteBanner:
        return self.banner


async def request(banner: SiteBanner, method: str = "GET") -> httpx.Response:
    app = create_web_app(None, status=StatusEndpoint(StubBanner(banner)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
        return await client.request(method, STATUS_PATH)


async def test_answers_current_banner() -> None:
    for banner in SiteBanner:
        response = await request(banner)
        assert response.status_code == 200
        assert response.json() == {"banner": banner.value}


async def test_site_on_other_domain_can_read_fresh_answer() -> None:
    response = await request(SiteBanner.NONE)
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "no-store"


async def test_head_is_supported() -> None:
    response = await request(SiteBanner.MAINTENANCE, method="HEAD")
    assert response.status_code == 200


async def test_no_route_without_status() -> None:
    transport = httpx.ASGITransport(app=create_web_app(None))
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
        response = await client.get(STATUS_PATH)
    assert response.status_code == 404
