"""Настройка секрета вебхуков Remnawave (задача 09)."""

import pytest
from pydantic import ValidationError

from tsproxy.config import Settings

REQUIRED = {
    "BOT_TOKEN": "1:abc",
    "REMNAWAVE_TOKEN": "rw",
    "ADMIN_IDS": "111",
    "ADMIN_CHAT_ID": "-100123",
    "ALERTS_THREAD_ID": "2",
    "BACKUPS_THREAD_ID": "3",
}
SECRET = "Abc123" * 6


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def make() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_secret_is_optional(env: pytest.MonkeyPatch) -> None:
    env.setenv("REMNAWAVE_WEBHOOK_SECRET", "")
    assert make().remnawave_webhook_secret is None


def test_secret_is_masked(env: pytest.MonkeyPatch) -> None:
    env.setenv("REMNAWAVE_WEBHOOK_SECRET", SECRET)
    settings = make()
    assert SECRET in settings.secret_values()
    assert SECRET not in repr(settings)


@pytest.mark.parametrize("secret", ["short123", "A" * 31, "A" * 31 + "-", "A" * 31 + "_"])
def test_secret_follows_panel_rules(env: pytest.MonkeyPatch, secret: str) -> None:
    """Панель 3.4.5 принимает только 32+ латинских букв и цифр."""
    env.setenv("REMNAWAVE_WEBHOOK_SECRET", secret)
    with pytest.raises(ValidationError):
        make()
