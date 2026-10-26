"""`/health` наружу говорит только «ok» или «проблема»."""

from dataclasses import dataclass

import httpx

from tsproxy.web.app import create_web_app
from tsproxy.web.health import HEALTH_PATH, HealthEndpoint


@dataclass
class StubProbe:
    healthy: bool

    async def is_healthy(self) -> bool:
        return self.healthy


async def request(healthy: bool, method: str = "GET") -> httpx.Response:
    app = create_web_app(None, None, HealthEndpoint(StubProbe(healthy)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
        return await client.request(method, HEALTH_PATH)


async def test_healthy_answers_ok() -> None:
    response = await request(healthy=True)
    assert response.status_code == 200
    assert response.text == "ok"
    assert response.headers["cache-control"] == "no-store"


async def test_problem_answers_503_without_details() -> None:
    response = await request(healthy=False)
    assert response.status_code == 503
    assert response.text == "проблема"


async def test_head_is_supported_for_uptimerobot() -> None:
    response = await request(healthy=True, method="HEAD")
    assert response.status_code == 200


async def test_no_route_without_health() -> None:
    transport = httpx.ASGITransport(app=create_web_app(None))
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
        response = await client.get(HEALTH_PATH)
    assert response.status_code == 404
