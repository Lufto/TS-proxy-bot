import pytest
from pydantic import ValidationError

from tsproxy.config import RunMode, Settings

REQUIRED = {
    "BOT_TOKEN": "1:abc",
    "REMNAWAVE_TOKEN": "rw",
    "ADMIN_IDS": "111, 222",
    "ADMIN_CHAT_ID": "-100123",
    "ALERTS_THREAD_ID": "2",
    "BACKUPS_THREAD_ID": "3",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def make() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_from_env(env: pytest.MonkeyPatch) -> None:
    settings = make()
    assert settings.admin_ids == frozenset({111, 222})
    assert settings.run_mode is RunMode.DEV
    assert "1:abc" not in repr(settings)


def test_missing_token_is_error(env: pytest.MonkeyPatch) -> None:
    env.delenv("BOT_TOKEN")
    with pytest.raises(ValidationError):
        make()
