"""Тексты и тарифы из `content/*.yaml`.

Ни одного текста в коде (задача «Тексты, экраны и кнопки»).
"""

import html
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from string import Formatter
from typing import Any

import yaml

TEXTS_FILE = "texts.yaml"
ADMIN_TEXTS_FILE = "admin_texts.yaml"
TARIFFS_FILE = "tariffs.yaml"


class ContentError(Exception):
    """Файл с текстами или тарифами неполный или с ошибкой. Бот с таким файлом не стартует."""


@dataclass(frozen=True)
class Tariff:
    id: str
    title: str
    days: int
    price: Decimal
    hidden: bool = False  # виден только администраторам (тестовый тариф за 15 ₽)


class Texts:
    """Тексты по ключам вида `menu.title`. Подстановки `{name}` экранируются для HTML."""

    def __init__(self, items: Mapping[str, str], source: str) -> None:
        self._items = dict(items)
        self._source = source

    def require(self, keys: Iterable[str]) -> None:
        missing = sorted(set(keys) - self._items.keys())
        if missing:
            raise ContentError(f"{self._source}: нет текстов {', '.join(missing)}")

    def get(self, key: str, **values: object) -> str:
        try:
            template = self._items[key]
        except KeyError:
            raise ContentError(f"{self._source}: нет текста {key}") from None
        escaped = {name: html.escape(str(value)) for name, value in values.items()}
        try:
            return template.format_map(escaped)
        except KeyError as error:
            raise ContentError(f"{self._source}: {key} ждёт подстановку {error}") from None

    def placeholders(self, key: str) -> set[str]:
        return {field for _, field, _, _ in Formatter().parse(self._items[key]) if field}


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ContentError(f"нет файла {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ContentError(f"{path}: ошибка YAML: {error}") from error


def _flatten(node: object, prefix: str, source: str) -> dict[str, str]:
    """{"menu": {"title": "…"}} → {"menu.title": "…"}."""
    if isinstance(node, str):
        if not node.strip():
            raise ContentError(f"{source}: пустой текст {prefix}")
        return {prefix: node}
    if isinstance(node, dict):
        flat: dict[str, str] = {}
        for key, value in node.items():
            flat |= _flatten(value, f"{prefix}.{key}" if prefix else str(key), source)
        return flat
    raise ContentError(f"{source}: {prefix or 'корень'} должен быть текстом или группой текстов")


def load_texts(path: Path) -> Texts:
    return Texts(_flatten(_read_yaml(path), "", path.name), path.name)


def _parse_price(raw: object, where: str) -> Decimal:
    # float из YAML (`405.0`) хранится неточно — цену пишем целым числом или строкой
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        raise ContentError(f"{where}: цена должна быть целым числом или строкой, а не {raw!r}")
    try:
        price = Decimal(str(raw))
    except InvalidOperation:
        raise ContentError(f"{where}: цена {raw!r} — не число") from None
    if price <= 0:
        raise ContentError(f"{where}: цена должна быть больше нуля")
    return price


def _parse_tariff(item: object, index: int) -> Tariff:
    where = f"{TARIFFS_FILE}, тариф №{index + 1}"
    if not isinstance(item, dict):
        raise ContentError(f"{where}: ожидается набор полей id, title, days, price")
    unknown = item.keys() - {"id", "title", "days", "price", "hidden"}
    if unknown:
        raise ContentError(f"{where}: лишние поля {', '.join(sorted(unknown))}")
    try:
        tariff_id, title, days = item["id"], item["title"], item["days"]
    except KeyError as error:
        raise ContentError(f"{where}: нет поля {error}") from None
    if not isinstance(tariff_id, str) or not tariff_id or not isinstance(title, str):
        raise ContentError(f"{where}: id и title — непустые строки")
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ContentError(f"{where}: days — целое число больше нуля")
    hidden = item.get("hidden", False)
    if not isinstance(hidden, bool):
        raise ContentError(f"{where}: hidden — true или false")
    return Tariff(tariff_id, title, days, _parse_price(item.get("price"), where), hidden)


def load_tariffs(path: Path) -> tuple[Tariff, ...]:
    raw = _read_yaml(path)
    items = raw.get("tariffs") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise ContentError(f"{TARIFFS_FILE}: нужен непустой список tariffs")
    tariffs = tuple(_parse_tariff(item, index) for index, item in enumerate(items))
    ids = [t.id for t in tariffs]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ContentError(f"{TARIFFS_FILE}: повторяются id {', '.join(duplicates)}")
    if all(t.hidden for t in tariffs):
        raise ContentError(f"{TARIFFS_FILE}: нет ни одного тарифа для клиентов")
    return tariffs


@dataclass(frozen=True)
class Content:
    texts: Texts
    admin_texts: Texts
    tariffs: tuple[Tariff, ...]

    def tariff(self, tariff_id: str) -> Tariff | None:
        return next((t for t in self.tariffs if t.id == tariff_id), None)


# Ключи, без которых бот не стартует. Каждая задача дописывает сюда тексты своих экранов.
REQUIRED_TEXTS: frozenset[str] = frozenset(
    {
        # задача 06: меню, «Подключиться», инструкции, «О сервисе», заглушки
        "commands.start",
        "commands.profile",
        "commands.buy",
        "commands.support",
        "buttons.connect",
        "buttons.trial",
        "buttons.tariffs",
        "buttons.profile",
        "buttons.instructions",
        "buttons.support",
        "buttons.about",
        "buttons.back",
        "buttons.instructions_back",
        "buttons.open_subscription",
        "buttons.copy_link",
        "buttons.platform_ios",
        "buttons.platform_android",
        "buttons.platform_windows",
        "buttons.platform_macos",
        "menu.title",
        "connect.active",
        "connect.inactive",
        "connect.no_account",
        "connect.unavailable",
        "trial.granted",
        "trial.already_used",
        "trial.pending",
        "trial.has_subscription",
        "about.text",
        "instructions.choose",
        "instructions.ios",
        "instructions.android",
        "instructions.windows",
        "instructions.macos",
        "throttle.callback",
        "throttle.message",
        "errors.generic",
        "errors.stale_button",
        # задача 08: профиль и устройства
        "buttons.devices",
        "buttons.renew",
        "buttons.profile_back",
        "buttons.delete_device",
        "profile.title",
        "profile.status_trial",
        "profile.status_trial_traffic_used_up",
        "profile.status_active",
        "profile.status_grace",
        "profile.status_expired",
        "profile.status_suspended",
        "profile.devices",
        "profile.devices_no_limit",
        "profile.traffic",
        "profile.no_account",
        "devices.title",
        "devices.title_no_limit",
        "devices.item",
        "devices.item_no_system",
        "devices.unknown_model",
        "devices.hint",
        "devices.empty",
        "devices.deleted",
        "devices.not_found",
        "units.days_one",
        "units.days_few",
        "units.days_many",
        "units.hours_one",
        "units.hours_few",
        "units.hours_many",
        "units.less_than_hour",
        # задача 16: обращения в поддержку
        "support.intro",
        "support.ticket_created",
        "support.flood",
        "support.unsupported",
        "support.not_delivered",
        "support.reply_prefix",
        "fallback.unknown_command",
        # задача 12: тарифы и оплата
        "buttons.pay",
        "buttons.tariffs_back",
        "tariffs.title",
        "tariffs.button",
        "tariffs.button_discount",
        "tariffs.button_hidden",
        "tariffs.not_available",
        "payment.description",
        "payment.screen",
        "payment.too_many",
        "payment.unavailable",
    }
)
REQUIRED_ADMIN_TEXTS: frozenset[str] = frozenset(
    {
        "alerts.prefix",
        "alerts.repeated",
        "alerts.remnawave_unavailable",
        "alerts.remnawave_at_start",
        "alerts.bot_error",
        # задача 16: обращения в поддержку
        "alerts.support_topic_failed",
        "support.topic_name",
        "support.topic_header",
        "support.no_username",
        "support.reply_unsupported",
        "support.reply_failed",
        "support.client_blocked",
        # задача 09: вебхуки Remnawave, сверка, связь с нодами
        "alerts.remnawave_webhook_forged",
        "alerts.reconcile_unavailable",
        "alerts.reconcile_error",
        "alerts.reconcile_failed",
        "alerts.node_down",
        "alerts.node_restored",
        "alerts.payments_unavailable",  # задача 12
    }
)


def load_content(directory: Path) -> Content:
    content = Content(
        texts=load_texts(directory / TEXTS_FILE),
        admin_texts=load_texts(directory / ADMIN_TEXTS_FILE),
        tariffs=load_tariffs(directory / TARIFFS_FILE),
    )
    content.texts.require(REQUIRED_TEXTS)
    content.admin_texts.require(REQUIRED_ADMIN_TEXTS)
    return content
