"""Общее для тестов интеграций: асинхронные тесты (плагин anyio) и клиент к поддельной панели."""

from collections.abc import Callable

import httpx
import pytest
from mock_panel import TOKEN, MockPanel

from tsproxy.config import RunMode
from tsproxy.integrations.remnawave import RemnawaveApiClient, create_http_client


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def panel() -> MockPanel:
    return MockPanel()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_client(panel: MockPanel, sleeps: list[float]) -> Callable[[RunMode], RemnawaveApiClient]:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def factory(mode: RunMode) -> RemnawaveApiClient:
        http = create_http_client(
            "https://panel.example", TOKEN, transport=httpx.MockTransport(panel.handle)
        )
        return RemnawaveApiClient(http, mode, prod_squad_name="Default-Squad", sleep=fake_sleep)

    return factory
