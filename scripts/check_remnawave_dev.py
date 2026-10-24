"""Ручная проверка обёртки Remnawave на настоящей панели (задача 05).

Создаёт, продлевает, выключает и удаляет ОДИН аккаунт `dev_<id>` в squad `test`,
а до и после снимает слепок всех остальных аккаунтов панели и сверяет: ни один не изменился.

Запуск (только с `RUN_MODE=dev` и токеном для разработки в `.env`):
    uv run python scripts/check_remnawave_dev.py --yes
Код выхода 0 — всё в порядке; 1 — что-то не так (подробности в журнале).
"""

import argparse
import asyncio
import hashlib
import logging
import sys
from datetime import UTC, datetime, timedelta

from remnawave import RemnawaveSDK

from tsproxy.config import RunMode, Settings, load_settings
from tsproxy.integrations.remnawave import RemnawaveApiClient, create_http_client
from tsproxy.integrations.remnawave_types import (
    DEV_SQUAD_NAME,
    AccountNotFound,
    AccountStatus,
)
from tsproxy.logging_setup import setup_logging

logger = logging.getLogger("check_remnawave_dev")

DEFAULT_TEST_TELEGRAM_ID = 999_000_001
TRIAL_DEVICE_LIMIT = 3
TRIAL_TRAFFIC_BYTES = 7 * 1024**3
FIRST_EXPIRY = timedelta(hours=1)
EXTENDED_EXPIRY = timedelta(days=2)
PAGE_SIZE = 500

type Snapshot = dict[int, tuple[object, ...]]


async def snapshot_all(sdk: RemnawaveSDK) -> Snapshot:
    """Слепок всех аккаунтов панели (только чтение). Ссылку подписки храним хэшем."""
    result: Snapshot = {}
    cursor: int | None = None
    while True:
        page = await sdk.users.get_users_stream(size=PAGE_SIZE, cursor=cursor)
        for u in page.users:
            result[u.id] = (
                u.username,
                u.status.value,
                u.expire_at.isoformat(),
                u.traffic_limit_bytes,
                u.hwid_device_limit,
                u.telegram_id,
                tuple(sorted(s.name for s in u.active_internal_squads)),
                hashlib.sha256(u.short_uuid.encode()).hexdigest()[:12],
                u.updated_at.isoformat(),
            )
        if not page.has_more or page.next_cursor is None:
            return result
        cursor = page.next_cursor


def compare(before: Snapshot, after: Snapshot, own_id: int | None) -> list[str]:
    """Чем отличаются слепки, не считая проверочного аккаунта."""
    problems = []
    for user_id in sorted(before.keys() | after.keys()):
        if user_id == own_id:
            continue
        if before.get(user_id) != after.get(user_id):
            name = (before.get(user_id) or after.get(user_id) or ("?",))[0]
            problems.append(f"аккаунт id={user_id} ({name}) изменился")
    return problems


def check(condition: bool, what: str) -> None:
    """Проверка сценария; при провале — выход с кодом 1."""
    if not condition:
        logger.error("Проверка не прошла: %s", what)
        raise SystemExit(1)


async def exercise(client: RemnawaveApiClient, telegram_id: int) -> int:
    """Сценарий над одним dev_-аккаунтом. Возвращает его id в панели."""
    version = await client.check_panel_version()
    logger.info("Версия панели: %s", version)
    nodes = await client.list_nodes()
    logger.info("Ноды: %s", ", ".join(f"{n.name}={n.is_connected}" for n in nodes))

    try:
        await client.get_account(telegram_id)
        logger.error("dev_%s уже существует — удалите его в панели и повторите", telegram_id)
        raise SystemExit(1)
    except AccountNotFound:
        logger.info("dev_%s нет — создаём", telegram_id)

    now = datetime.now(UTC)
    account = await client.get_or_create_account(
        telegram_id,
        expire_at=now + FIRST_EXPIRY,
        device_limit=TRIAL_DEVICE_LIMIT,
        traffic_limit_bytes=TRIAL_TRAFFIC_BYTES,
    )
    check(account.squad_names == {DEV_SQUAD_NAME}, "account.squad_names == {DEV_SQUAD_NAME}")
    logger.info("Создан %s (id=%s), статус %s", account.username, account.id, account.status)

    again = await client.get_or_create_account(
        telegram_id, expire_at=now + FIRST_EXPIRY, device_limit=3, traffic_limit_bytes=0
    )
    check(again.id == account.id, "again.id == account.id")
    logger.info("Повторное создание вернуло тот же аккаунт")

    target = (now + EXTENDED_EXPIRY).replace(microsecond=0)
    extended = await client.set_expiry(telegram_id, target)
    check(extended.expire_at == target, "extended.expire_at == target")
    logger.info("Продлён до %s", extended.expire_at.isoformat())

    limited = await client.set_limits(telegram_id, device_limit=3, traffic_limit_bytes=0)
    check(limited.device_limit == TRIAL_DEVICE_LIMIT, "limited.device_limit == TRIAL_DEVICE_LIMIT")
    check(limited.traffic_limit_bytes == 0, "limited.traffic_limit_bytes == 0")

    logger.info("Устройств: %d", len(await client.list_devices(telegram_id)))
    revoked = await client.revoke_subscription(telegram_id)
    check(
        revoked.subscription_url != account.subscription_url,
        "revoked.subscription_url != account.subscription_url",
    )
    logger.info("Ссылка подписки перевыпущена")

    disabled = await client.disable_account(telegram_id)
    check(disabled.status is AccountStatus.DISABLED, "disabled.status is AccountStatus.DISABLED")
    await client.disable_account(telegram_id)  # повтор не должен падать
    logger.info("Выключен")

    own = await client.list_accounts()
    logger.info("Своих аккаунтов в режиме dev: %d", len(own))

    await client.delete_account(telegram_id)
    try:
        await client.get_account(telegram_id)
        logger.error("Аккаунт не удалился")
        raise SystemExit(1)
    except AccountNotFound:
        logger.info("Удалён")
    return account.id


async def run(settings: Settings, telegram_id: int) -> int:
    http = create_http_client(settings.remnawave_url, settings.remnawave_token.get_secret_value())
    client = RemnawaveApiClient(http, RunMode.DEV, prod_squad_name="unused-in-dev")
    sdk = RemnawaveSDK(client=http)
    try:
        before = await snapshot_all(sdk)
        logger.info("Слепок до: %d аккаунтов", len(before))
        own_id = await exercise(client, telegram_id)
        after = await snapshot_all(sdk)
        logger.info("Слепок после: %d аккаунтов", len(after))
    finally:
        await client.aclose()
    problems = compare(before, after, own_id)
    for problem in problems:
        logger.error("ЧУЖОЙ АККАУНТ ИЗМЕНИЛСЯ: %s", problem)
    if problems:
        return 1
    logger.info("Готово: чужие аккаунты не изменились")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telegram-id", type=int, default=DEFAULT_TEST_TELEGRAM_ID)
    parser.add_argument("--yes", action="store_true", help="подтверждаю запуск на живой панели")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging("INFO", settings.secret_values())
    if settings.run_mode is not RunMode.DEV:
        logger.error("Только для RUN_MODE=dev")
        return 1
    if not args.yes:
        logger.error("Запуск меняет живую панель (один dev_-аккаунт). Добавьте --yes")
        return 1
    return asyncio.run(run(settings, args.telegram_id))


if __name__ == "__main__":
    sys.exit(main())
