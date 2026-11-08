"""Поддельная панель Remnawave по HTTP (httpx.MockTransport) для тестов настоящего клиента."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

TOKEN = "panel-token-secret-value"


type Reply = httpx.Response | Callable[[httpx.Request], httpx.Response]


def ok(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"response": payload})


def fail(status: int, payload: Any) -> httpx.Response:
    return httpx.Response(status, json=payload)


@dataclass
class MockPanel:
    """Отвечает на запросы по таблице (метод, путь) → ответ или очередь ответов."""

    routes: dict[tuple[str, str], list[Reply]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def on(self, method: str, path: str, *replies: Reply) -> None:
        self.routes.setdefault((method, path), []).extend(replies)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            raise AssertionError(f"Неожиданный запрос: {request.method} {request.url.path}")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return reply(request) if callable(reply) else reply

    @property
    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.writes[index].content)
