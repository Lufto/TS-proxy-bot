"""Общее для тестов вебхуков Remnawave и сверки: тревоги, ноды, тексты администраторов."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from conftest import NOW

from tsproxy.content.loader import Texts, load_content
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import Node
from tsproxy.services.account_checks import AccountCheck
from tsproxy.services.alerts import Alerter
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
REPO_CONFIG = Path(__file__).parent.parent.parent.parent / "config"
CHAT_ID = -100123
THREAD_ID = 2
NODE_UUID = "4c1a6c7e-0000-4000-8000-000000000001"


def admin_texts() -> Texts:
    return load_content(REPO_CONTENT, REPO_CONFIG).admin_texts


def make_alerter(sender: FakeAlertSender, clock: FakeClock, mask: Masker) -> Alerter:
    return Alerter(sender, admin_texts(), CHAT_ID, THREAD_ID, clock, mask)


def make_node(
    *,
    connected: bool,
    changed_at: datetime | None,
    disabled: bool = False,
    uuid: str = NODE_UUID,
    name: str = "Нидерланды-1",
) -> Node:
    return Node(
        uuid=uuid,
        name=name,
        is_connected=connected,
        is_disabled=disabled,
        last_status_change=changed_at,
        last_status_message=None if connected else "connect ECONNREFUSED",
        users_online=None,
    )


@dataclass
class RecordingHandler:
    """Обработчик проверок-подделка: запоминает проверки, по желанию падает."""

    checks: list[AccountCheck] = field(default_factory=list)
    error: Exception | None = None

    async def handle(self, check: AccountCheck) -> None:
        self.checks.append(check)
        if self.error is not None:
            raise self.error


async def create_account(remnawave: FakeRemnawave, telegram_id: int) -> None:
    """Аккаунт бота на 7 дней от `NOW` (создавать до того, как тест сдвинет часы)."""
    await remnawave.get_or_create_account(
        telegram_id,
        expire_at=NOW + timedelta(days=7),
        device_limit=3,
        traffic_limit_bytes=0,
    )
