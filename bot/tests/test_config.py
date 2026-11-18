import pytest
from pydantic import ValidationError

from tsproxy.config import PaymentsMode, RunMode, Settings, describe_settings_error

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


# Выдуманные ключи Platega: настоящие живут только в `.env` (задача 15)
PLATEGA_ENV = {
    "PLATEGA_MERCHANT_ID": "merchant-for-tests",
    "PLATEGA_SECRET": "secret-for-tests",
    "BOT_USERNAME": "ts_proxy_test_bot",
}


def set_platega(env: pytest.MonkeyPatch) -> None:
    for key, value in PLATEGA_ENV.items():
        env.setenv(key, value)


def test_settings_from_env(env: pytest.MonkeyPatch) -> None:
    settings = make()
    assert settings.admin_ids == frozenset({111, 222})
    assert settings.run_mode is RunMode.DEV
    assert "1:abc" not in repr(settings)


def test_missing_token_is_error(env: pytest.MonkeyPatch) -> None:
    env.delenv("BOT_TOKEN")
    with pytest.raises(ValidationError):
        make()


def test_settings_error_names_variables_without_values(env: pytest.MonkeyPatch) -> None:
    """Ошибка `.env` для журнала: какие переменные, без значений (там бывают секреты)."""
    env.delenv("BOT_TOKEN")
    env.setenv("ADMIN_CHAT_ID", "secret-looking-value")
    with pytest.raises(ValidationError) as caught:
        make()

    described = describe_settings_error(caught.value)

    assert "BOT_TOKEN" in described
    assert "ADMIN_CHAT_ID" in described
    assert "secret-looking-value" not in described


def test_fake_payments_are_default_in_dev(env: pytest.MonkeyPatch) -> None:
    assert make().payments_mode is PaymentsMode.FAKE


def test_fake_payments_refuse_prod(env: pytest.MonkeyPatch) -> None:
    """Аудит R23: в бою поддельная платёжка засчитывала бы оплату, которой не было."""
    env.setenv("RUN_MODE", "prod")
    env.setenv("PUBLIC_BASE_URL", "https://bot.ts-proxy.com")
    env.setenv("TELEGRAM_WEBHOOK_SECRET", "A" * 40)
    env.setenv("REMNAWAVE_WEBHOOK_SECRET", "R" * 40)
    env.setenv("PAYMENTS_MODE", "fake")
    with pytest.raises(ValidationError, match="PAYMENTS_MODE=fake"):
        make()

    env.setenv("PAYMENTS_MODE", "platega")
    set_platega(env)
    assert make().payments_mode is PaymentsMode.PLATEGA


def test_prod_requires_remnawave_webhook_secret(env: pytest.MonkeyPatch) -> None:
    """Без секрета бот на сервере узнавал бы об окончаниях сроков с опозданием до 5 минут."""
    env.setenv("RUN_MODE", "prod")
    env.setenv("PUBLIC_BASE_URL", "https://bot.ts-proxy.com")
    env.setenv("TELEGRAM_WEBHOOK_SECRET", "A" * 40)
    env.setenv("PAYMENTS_MODE", "platega")
    set_platega(env)
    with pytest.raises(ValidationError, match="REMNAWAVE_WEBHOOK_SECRET"):
        make()

    env.setenv("REMNAWAVE_WEBHOOK_SECRET", "R" * 40)
    assert make().remnawave_webhook_secret is not None


@pytest.mark.parametrize("run_mode", ["dev", "prod"])
def test_payments_off_is_allowed_everywhere(env: pytest.MonkeyPatch, run_mode: str) -> None:
    """Задача 29: тестовый запуск без платёжки — и на Mac, и на сервере."""
    env.setenv("RUN_MODE", run_mode)
    env.setenv("PUBLIC_BASE_URL", "https://bot.ts-proxy.com")
    env.setenv("TELEGRAM_WEBHOOK_SECRET", "A" * 40)
    env.setenv("REMNAWAVE_WEBHOOK_SECRET", "R" * 40)
    env.setenv("PAYMENTS_MODE", "off")

    assert make().payments_mode is PaymentsMode.OFF


# --- Platega (задача 15) ---


@pytest.mark.parametrize("missing", list(PLATEGA_ENV))
def test_platega_needs_keys_and_bot_name(env: pytest.MonkeyPatch, missing: str) -> None:
    env.setenv("PAYMENTS_MODE", "platega")
    set_platega(env)
    env.delenv(missing)
    with pytest.raises(ValidationError, match="PAYMENTS_MODE=platega"):
        make()


def test_platega_bot_name_without_at(env: pytest.MonkeyPatch) -> None:
    env.setenv("PAYMENTS_MODE", "platega")
    set_platega(env)
    env.setenv("BOT_USERNAME", "@ts_proxy_bot")
    with pytest.raises(ValidationError, match="BOT_USERNAME"):
        make()


def test_platega_keys_are_masked(env: pytest.MonkeyPatch) -> None:
    env.setenv("PAYMENTS_MODE", "platega")
    set_platega(env)
    settings = make()
    assert settings.payments_mode is PaymentsMode.PLATEGA
    assert {"merchant-for-tests", "secret-for-tests"} <= set(settings.secret_values())
    assert "secret-for-tests" not in repr(settings)


def test_platega_test_account_refuses_prod(env: pytest.MonkeyPatch) -> None:
    """Аудит R08: в бою засчитываются только настоящие платежи — тестовый кабинет нельзя."""
    env.setenv("RUN_MODE", "prod")
    env.setenv("PUBLIC_BASE_URL", "https://bot.ts-proxy.com")
    env.setenv("TELEGRAM_WEBHOOK_SECRET", "A" * 40)
    env.setenv("REMNAWAVE_WEBHOOK_SECRET", "R" * 40)
    env.setenv("PAYMENTS_MODE", "platega")
    set_platega(env)
    env.setenv("PLATEGA_TEST_ACCOUNT", "true")
    with pytest.raises(ValidationError, match="PLATEGA_TEST_ACCOUNT"):
        make()

    env.setenv("PLATEGA_TEST_ACCOUNT", "false")
    assert make().platega_test_account is False
