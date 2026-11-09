"""Тексты из папок `content/client/`, `content/admin/`, `content/alerts/`
и тарифы из общего конфига `config/tariffs.yaml`.

Ни одного текста в коде (задача «Тексты, экраны и кнопки»).
"""

import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from string import Formatter
from typing import Any

import yaml

from tsproxy.domain.site_banner import SiteBanner

# Тексты клиента — из client/, администратора — из admin/ и alerts/; все .yaml папки — один набор
CLIENT_FOLDERS = ("client",)
ADMIN_FOLDERS = ("admin", "alerts")
TEXTS_SUFFIX = ".yaml"
TARIFFS_FILE = "tariffs.yaml"
STARS_RATE_KEY = "stars_rub_rate"  # курс звезды Telegram в рублях (задача 33)
# Быстрые ответы поддержки в admin/: quick_replies.<имя>.button и .text
QUICK_REPLIES_GROUP = "quick_replies"
QUICK_PARTS = ("button", "text")
QUICK_REPLY_ID_MAX_LENGTH = 32
QUICK_REPLY_ID_PATTERN = rf"[a-z0-9_]{{1,{QUICK_REPLY_ID_MAX_LENGTH}}}"
QUICK_REPLY_ID = re.compile(QUICK_REPLY_ID_PATTERN)


class ContentError(Exception):
    """Файл с текстами или тарифами неполный или с ошибкой. Бот с таким файлом не стартует."""


@dataclass(frozen=True)
class Tariff:
    id: str
    title: str
    days: int
    price: Decimal


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

    def ids_under(self, prefix: str) -> list[str]:
        """Имена групп внутри `prefix` в порядке файла: `quick_replies.hello.text` → `hello`."""
        start = f"{prefix}."
        ids: list[str] = []
        for key in self._items:
            if key.startswith(start):
                group = key.removeprefix(start).split(".", 1)[0]
                if group not in ids:
                    ids.append(group)
        return ids


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


def _text_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise ContentError(f"нет папки с текстами {folder}")
    files = sorted(folder.glob(f"*{TEXTS_SUFFIX}"))
    if not files:
        raise ContentError(f"в папке {folder} нет файлов {TEXTS_SUFFIX}")
    return files


def load_texts(*folders: Path) -> Texts:
    """Все `.yaml` из папок — один набор. Одинаковый ключ в двух файлах — ошибка."""
    items: dict[str, str] = {}
    origin: dict[str, str] = {}
    for folder in folders:
        for path in _text_files(folder):
            source = f"{folder.name}/{path.name}"
            for key, text in _flatten(_read_yaml(path), "", source).items():
                if key in items:
                    raise ContentError(f"текст {key} есть и в {origin[key]}, и в {source}")
                items[key] = text
                origin[key] = source
    return Texts(items, ", ".join(f"{folder.name}/" for folder in folders))


def load_client_texts(directory: Path) -> Texts:
    return load_texts(*(directory / folder for folder in CLIENT_FOLDERS))


def load_admin_texts(directory: Path) -> Texts:
    return load_texts(*(directory / folder for folder in ADMIN_FOLDERS))


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
    unknown = item.keys() - {"id", "title", "days", "price"}
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
    return Tariff(tariff_id, title, days, _parse_price(item.get("price"), where))


def load_stars_rate(path: Path) -> Decimal:
    """Курс звезды Telegram в рублях (`stars_rub_rate`, задача 33). Не больше нуля — не стартуем.

    Цены в звёздах бот считает из рублёвых по этому курсу (`domain.stars.stars_price`).
    """
    raw = _read_yaml(path)
    value = raw.get(STARS_RATE_KEY) if isinstance(raw, dict) else None
    where = f"{TARIFFS_FILE}: {STARS_RATE_KEY}"
    # 1.5 из YAML — float; через строку он превращается в точное Decimal("1.5")
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ContentError(f"{where} — курс звезды в рублях, например 1.5, а не {value!r}")
    try:
        rate = Decimal(str(value))
    except InvalidOperation:
        raise ContentError(f"{where} {value!r} — не число") from None
    if not rate.is_finite() or rate <= 0:
        raise ContentError(f"{where} должен быть больше нуля, а не {value!r}")
    return rate


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
    return tariffs


@dataclass(frozen=True)
class Content:
    texts: Texts
    admin_texts: Texts
    tariffs: tuple[Tariff, ...]
    stars_rub_rate: Decimal  # курс звезды Telegram в рублях (задача 33)

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
        "buttons.platform_linux",
        "menu.title",
        "connect.active",
        "connect.inactive",
        "connect.no_account",
        "connect.unavailable",
        "connect.pending",
        "trial.granted",
        "trial.already_used",
        "trial.pending",
        "trial.has_subscription",
        "trial.disabled",  # задача 28
        "about.text",
        "instructions.choose",
        "instructions.ios",
        "instructions.android",
        "instructions.windows",
        "instructions.macos",
        "instructions.linux",
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
        # задача 31: клиент сам перевыпускает ссылку подписки
        "buttons.revoke_link",
        "buttons.confirm_revoke_link",
        "buttons.cancel_revoke_link",
        "revoke_link.ask",
        "revoke_link.too_soon",
        "revoke_link.done",
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
        # задача 17: частые вопросы на экране «💬 Поддержка» (набор — FaqQuestion)
        "buttons.faq_back",
        *(
            f"faq.{question}.{part}"
            for question in ("connect", "not_working", "devices", "renew", "payment")
            for part in ("button", "answer")
        ),
        # задача 12: тарифы и оплата
        "buttons.pay",
        "buttons.tariffs_back",
        "tariffs.title",
        "tariffs.button",
        "tariffs.button_discount",
        "tariffs.not_available",
        "payment.description",
        "payment.screen",
        "payment.too_many",
        "payment.unavailable",
        "payment.disputed",  # задача 15: спор через банк
        "payment.disputed_ended",
        "payment.disputed_pending",
        # задача 29: оплата выключена
        "tariffs.off_title",
        "tariffs.off_trial",
        "tariffs.off_later",
        # задача 13: оплата засчитана
        "payment.paid",
        "payment.paid_prorated",
        "payment.pending",
        # задача 14: проверка оплаты, двойная оплата, задержка доступа
        "buttons.check_payment",
        "payment.delayed",
        "payment.double",
        "payment.found",
        "payment.check_not_paid",
        "payment.check_canceled",
        "payment.check_already",
        "payment.check_not_found",
        "payment.check_problem",
        "payment.check_unavailable",
        # задача 11: льготный период
        "grace.started",
        "grace.reminder",
        "grace.last_day",
        "grace.ended",
        # задача 10: напоминания, продления пробного, пробный трафик
        "notices.trial_day_before",
        "notices.trial_hour_before",
        "notices.paid_day_before",
        "notices.paid_hour_before",
        "notices.trial_extended",
        "notices.trial_farewell",
        "notices.trial_traffic_warning",
        "notices.trial_traffic_used_up",
        # задача 20: извинение после сбоя
        "compensation.extended",
        "compensation.grace_extended",
        # задача 21: сообщение о возврате, перенос подписки
        "refund.completed",
        "refund.completed_access_ended",
        "transfer.received",
        "manual_grant.added",
        # задача 33: оплата звёздами Telegram
        "commands.paysupport",
        "buttons.pay_sbp",
        "buttons.pay_stars",
        "units.rubles",
        "units.stars",
        "tariffs.screen",
        "stars.invoice_title",
        "stars.invoice_description",
        "stars.price_label",
        "stars.pay_button",
        "stars.invoice_sent",
        *(
            f"stars.refused.{key}"
            for key in ("not_found", "already_paid", "expired", "amount_mismatch", "error")
        ),
        "stars.returned",
        "stars.return_failed",
        "stars.unrecorded",
    }
)
REQUIRED_ADMIN_TEXTS: frozenset[str] = frozenset(
    {
        "alerts.prefix",
        "alerts.repeated",
        "alerts.remnawave_unavailable",
        "alerts.remnawave_at_start",
        "alerts.bot_error",
        # задача 29: оплата выключена
        "alerts.payments_off",
        # задача 16: обращения в поддержку
        "alerts.support_topic_failed",
        "support.topic_name",
        "support.no_username",
        "support.reply_unsupported",
        "support.reply_failed",
        "support.client_blocked",
        # задача 17: карточка клиента, кнопки темы, напоминание, быстрые ответы
        *(
            f"support.card.{key}"
            for key in (
                "title",
                "title_reopened",
                "client",
                "subscription_none",
                "subscription_trial",
                "subscription_trial_traffic_used_up",
                "subscription_active",
                "subscription_grace",
                "subscription_expired",
                "subscription_suspended",
                "subscription_unknown",
                "devices",
                "devices_no_limit",
                "payments",
                "payments_none",
                "trial",
                "trial_none",
                "hint",
            )
        ),
        *(
            f"support.buttons.{key}"
            for key in ("extend", "devices", "refund", "take", "close", "quick_replies", "hide")
        ),
        "support.taken",
        "support.closed",
        "support.already_yours",
        "support.already_closed",
        "support.take_closed",
        "support.ticket_not_found",
        "support.quick_reply_not_found",
        "support.reminder",
        "support.reminder_assignee",
        "support.quick_replies_title",
        "support.quick_reply_sent",
        # задача 09: вебхуки Remnawave, сверка, связь с нодами
        "alerts.remnawave_webhook_forged",
        "alerts.reconcile_unavailable",
        "alerts.reconcile_error",
        "alerts.reconcile_failed",
        "alerts.node_down",
        "alerts.node_restored",
        "alerts.payments_unavailable",  # задача 12
        # задача 22: сторож фоновых задач и бэкапы панели
        "alerts.job_stale",
        "alerts.job_stale_never",
        "alerts.job_hint_running",
        "alerts.job_hint_failing",
        "alerts.job_recovered",
        "alerts.panel_backup_stale",
        "alerts.panel_backup_never",
        "alerts.panel_backup_not_sent",
        "alerts.panel_backup_unreadable",
        # задача 22: /proof — доказательство оказания услуги
        "admin.proof.usage",
        "admin.proof.not_found",
        "admin.proof.caption",
        "admin.proof.header",
        "admin.proof.service",
        "admin.proof.client",
        "admin.proof.order",
        "admin.proof.payment",
        "admin.proof.payment_none",
        "admin.proof.grant_target",
        "admin.proof.account",
        "admin.proof.never_connected",
        "admin.proof.account_none",
        "admin.proof.panel_unavailable",
        "admin.proof.devices_title",
        "admin.proof.device",
        "admin.proof.events_title",
        "admin.proof.event",
        "admin.proof.event_admin",
        "admin.proof.repeated",
        "admin.proof.truncated",
        # задача 22: ежедневный отчёт
        "report.title",
        "report.new_clients",
        "report.trials",
        "report.payments",
        "report.unfinished_orders",
        "report.alerts",
        "report.alerts_since_start",
        "report.bot_backup_sent",
        "report.bot_backup_not_sent",
        "report.bot_backup_sent_unknown",
        "report.bot_backup_stale",
        "report.bot_backup_none",
        "report.bot_backup_unreadable",
        "report.bot_backup_off",
        "report.panel_backup_ok",
        "report.panel_backup_not_sent",
        "report.panel_backup_stale",
        "report.panel_backup_none",
        "report.panel_backup_unreadable",
        "report.panel_backup_off",
        "report.jobs_ok",
        "report.jobs_stale",
        # задача 18: админка — поиск, карточка клиента, действия
        "admin.usage",
        "admin.give_usage",
        "admin.days_out_of_range",
        "admin.not_found",
        "admin.client_not_found",
        "admin.many_found",
        "admin.many_found_item",
        "admin.panel_unavailable",
        "admin.cancelled",
        "admin.not_admin",
        "admin.no_value",
        "admin.card.title",
        "admin.card.blocked",
        "admin.card.status_trial",
        "admin.card.status_trial_traffic_used_up",
        "admin.card.status_active",
        "admin.card.status_grace",
        "admin.card.status_expired",
        "admin.card.status_suspended",
        "admin.card.no_account",
        "admin.card.panel_unavailable",
        "admin.card.traffic",
        "admin.card.devices",
        "admin.card.devices_no_limit",
        "admin.card.grace",
        "admin.card.trial_none",
        "admin.card.trial_delivered",
        "admin.card.trial_pending",
        "admin.card.payments_none",
        "admin.card.payments",
        "admin.card.payment_item",
        "admin.order_status.created",
        "admin.order_status.paid",
        "admin.order_status.access_granted",
        "admin.order_status.access_failed",
        "admin.order_status.manual_review",
        "admin.order_status.canceled",
        "admin.order_status.refunded",
        "admin.buttons.extend",
        "admin.buttons.devices",
        "admin.buttons.reset_trial",
        "admin.buttons.revoke",
        "admin.buttons.confirm_extend",
        "admin.buttons.confirm_reset",
        "admin.buttons.confirm_revoke",
        "admin.buttons.cancel",
        "admin.buttons.delete_device",
        "admin.extend.preview",
        "admin.extend.no_account",
        "admin.extend.done",
        "admin.extend.changed",
        "admin.reset.ask",
        "admin.reset.done",
        "admin.reset.not_used",
        "admin.reset.has_paid",
        "admin.revoke.ask",
        "admin.revoke.done",
        "admin.revoke.no_account",
        "admin.devices.title",
        "admin.devices.title_no_limit",
        "admin.devices.item",
        "admin.devices.unknown",
        "admin.devices.empty",
        "admin.devices.no_account",
        "admin.devices.deleted",
        "admin.devices.not_found",
        # задача 13: зачёт оплаты и выдача доступа
        "alerts.payment_amount_mismatch",
        "alerts.payment_not_recorded",
        "alerts.grant_delayed",
        "alerts.grant_failed",
        "alerts.grant_manual",
        "payments.received",
        "payments.received_prorated",
        "payments.no_username",
        # задача 14: уведомления платёжки, проверки оплат, сверка, двойная оплата
        "payments.possible_double",
        "alerts.payment_notification_forged",
        "alerts.payment_rejected",
        "alerts.payment_checks_unavailable",
        "alerts.payments_reconcile",
        "alerts.payments_reconcile_unavailable",
        # задача 15: Platega — отказы, спор через банк, изменения статуса оплаты
        "alerts.payments_reconcile_failed",
        "alerts.payment_create_rejected",
        "alerts.payment_check_refused",
        "alerts.payment_chargeback",
        *(
            f"alerts.chargeback_access.{key}"
            for key in ("shortened", "ended", "no_account", "failed")
        ),
        "alerts.payment_refunded_outside_bot",
        "alerts.payment_status_changed",
        "alerts.refund_review_overdue",
        *(
            f"payments.status.{key}"
            for key in (
                "pending",
                "succeeded",
                "canceled",
                "refunded",
                "partially_refunded",
                "disputed",
                "unknown",
            )
        ),
        *(
            f"payments.reconcile.{key}"
            for key in (
                "paid_without_order",
                "credited_now",
                "not_credited",
                "credited_not_paid",
                "missing_at_provider",
                "refunded_outside_bot",
            )
        ),
        # задача 21: возвраты через поддержку и перенос подписки
        "alerts.refund_provider_unavailable",
        "alerts.refund_provider_rejected",
        "alerts.refund_term_pending",
        "alerts.refund_term_changed",
        "alerts.transfer_source_not_ended",
        "refunds.button",
        "refunds.list.title",
        "refunds.list.empty",
        "refunds.list.item",
        "refunds.buttons.order",
        "refunds.buttons.confirm",
        "refunds.ask",
        "refunds.duration",
        "refunds.duration_hours",
        *(
            f"refunds.state.{key}"
            for key in (
                "available",
                "approved",
                "in_review",
                "refunded",
                "completed",
                "not_granted",
                "transferred",
                "payments_off",
                "disputed",
            )
        ),
        *(
            f"refunds.result.{key}"
            for key in (
                "completed",
                "completed_access_ended",
                "term_pending",
                "in_review",
                "provider_pending",
                "failed",
            )
        ),
        *(
            f"refunds.refused.{key}"
            for key in (
                "payments_off",
                "client_not_found",
                "order_not_found",
                "not_granted",
                "window_passed",
                "already_refunded",
                "transferred",
                "disputed",
            )
        ),
        *(
            f"refunds.topic.{key}"
            for key in (
                "header",
                "by_admin",
                "completed",
                "term_pending",
                "in_review",
                "provider_pending",
                "failed",
            )
        ),
        *(
            f"admin.transfer.{key}"
            for key in (
                "usage",
                "preview",
                "no_subscription",
                "left",
                "confirm",
                "same_client",
                "source_not_found",
                "target_not_found",
                "payment_not_found",
                "nothing_to_transfer",
                "refund_open",
                "changed",
                "done",
            )
        ),
        # задача 28: выключатель пробных и автовыключение при спаме
        "alerts.trials_disabled",
        "alerts.trials_enabled",
        "alerts.trials_spam",
        "admin.trials.enabled",
        "admin.trials.disabled",
        "admin.trials.switched",
        "admin.trials.unchanged",
        "admin.trials.disable_button",
        "admin.trials.enable_button",
        # задачи сайта 14, 15: плашка о неполадках на сайте
        "alerts.site_banner_set",
        "admin.site_banner.state",
        "admin.site_banner.switched",
        "admin.site_banner.unchanged",
        "admin.site_banner.alert_button",
        *(
            f"admin.site_banner.{group}.{banner.value}"
            for group in ("names", "buttons")
            for banner in SiteBanner
        ),
        # задача 20: компенсация при сбое ноды
        "alerts.compensation_failed",
        "admin.compensation.offer",
        "admin.compensation.button",
        "admin.compensation.confirm",
        "admin.compensation.confirm_button",
        "admin.compensation.resume_button",
        "admin.compensation.in_progress",
        "admin.compensation.started",
        "admin.compensation.progress",
        "admin.compensation.finished",
        "admin.compensation.unfinished",
        "admin.compensation.manual",
        "admin.compensation.nothing",
        "admin.compensation.launched",
        "admin.compensation.resumed",
        "admin.compensation.running",
        "admin.compensation.not_found",
        "admin.compensation.still_open",
        # задача 20: /outages
        *(
            f"admin.outages.{key}"
            for key in (
                "empty",
                "title",
                "open",
                "waiting",
                "covered",
                "compensated",
                "footer",
                "ask_button",
                "resume_button",
            )
        ),
        # задача 19: статистика и рассылка
        *(
            f"admin.stats.{key}"
            for key in (
                "title",
                "sales_today",
                "sales_month",
                "subscriptions",
                "subscriptions_unknown",
                "trials",
                "trials_none",
                "support",
                "response",
                "response_none",
                "clients",
                "duration_minutes",
                "duration_hours",
                "duration_under_minute",
                "stars_today",  # задача 33
                "stars_month",
            )
        ),
        # задача 33: оплата звёздами Telegram
        "units.rubles",
        "units.stars",
        "payments.stars",
        "report.payments_stars",
        "alerts.stars_returned",
        "alerts.stars_not_returned",
        "alerts.stars_unrecorded",
        *(
            f"alerts.stars_reason.{key}"
            for key in ("unknown_order", "other_charge", "not_credited")
        ),
        *(
            f"admin.broadcast.{key}"
            for key in (
                "usage",
                "preview_header",
                "preview_failed",
                "copy_header",
                "reply_or_text",
                "album",
                "unsupported",
                "has_buttons",
                "preview_missing",
                "send_button",
                "cancel_button",
                "stop_button",
                "already_handled",
                "busy",
                "no_recipients",
                "preview_expired",
                "cancelled",
                "stopping",
                "not_running",
                "started",
                "progress",
                "counts",
                "completed",
                "stopped",
                "aborted",
                "crashed",
            )
        ),
        # задача 35: объявление клиентам в приложении
        *(
            f"admin.announce.{key}"
            for key in (
                "usage",
                "none",
                "current",
                "published",
                "cleared",
                "already_off",
                "too_long",
                "no_rights",
            )
        ),
    }
)


def quick_reply_ids(admin_texts: Texts) -> list[str]:
    """Имена быстрых ответов поддержки. Имя попадает в данные кнопки — поэтому
    только латиница, цифры и `_`; у каждой заготовки должны быть `button` и `text`."""
    ids = admin_texts.ids_under(QUICK_REPLIES_GROUP)
    if not ids:
        raise ContentError(
            f"{ADMIN_FOLDERS[0]}/: нужна хотя бы одна заготовка в {QUICK_REPLIES_GROUP}"
        )
    for reply_id in ids:
        if not QUICK_REPLY_ID.fullmatch(reply_id):
            raise ContentError(
                f"{ADMIN_FOLDERS[0]}/: имя заготовки «{reply_id}» — латиница, цифры и _, "
                f"до {QUICK_REPLY_ID_MAX_LENGTH} символов"
            )
        admin_texts.require(f"{QUICK_REPLIES_GROUP}.{reply_id}.{part}" for part in QUICK_PARTS)
    return ids


def load_content(directory: Path, config_dir: Path) -> Content:
    """Тексты бота — из `directory`, тарифы — из общего конфига сервиса `config_dir`.

    Общий конфиг читает и сайт, поэтому цены на сайте и в боте не расходятся.
    """
    content = Content(
        texts=load_client_texts(directory),
        admin_texts=load_admin_texts(directory),
        tariffs=load_tariffs(config_dir / TARIFFS_FILE),
        stars_rub_rate=load_stars_rate(config_dir / TARIFFS_FILE),
    )
    content.texts.require(REQUIRED_TEXTS)
    content.admin_texts.require(REQUIRED_ADMIN_TEXTS)
    quick_reply_ids(content.admin_texts)
    return content
