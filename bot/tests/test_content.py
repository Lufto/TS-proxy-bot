from decimal import Decimal
from pathlib import Path

import pytest

from tsproxy.content.loader import (
    ContentError,
    check_alert_texts,
    load_content,
    load_stars_rate,
    load_tariffs,
    load_texts,
    quick_reply_ids,
)
from tsproxy.integrations import remnawave_types
from tsproxy.integrations.failures import ServiceError
from tsproxy.integrations.payments_types import PaymentStatus

REPO_CONTENT = Path(__file__).parent.parent / "content"
REPO_CONFIG = Path(__file__).parent.parent.parent / "config"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_folder(tmp_path: Path, text: str) -> Path:
    """Папка текстов с одним файлом — для проверок одного текста."""
    return write(tmp_path / "client" / "t.yaml", text).parent


def test_repo_content_loads() -> None:
    content = load_content(REPO_CONTENT, REPO_CONFIG)
    assert content.tariff("m3") is not None
    assert content.tariff("m3").price == Decimal("405")
    # задача 15: тестового тарифа за 15 ₽ нет, все тарифы — от минимума Platega (50 ₽)
    assert [t.id for t in content.tariffs] == ["m1", "m3", "m6", "m12"]


def test_texts_are_flattened_by_screen(tmp_path: Path) -> None:
    texts = load_texts(write_folder(tmp_path, "menu:\n  title: Привет\n"))
    assert texts.get("menu.title") == "Привет"


def test_substitutions_are_html_escaped(tmp_path: Path) -> None:
    texts = load_texts(write_folder(tmp_path, "hello: 'Привет, <b>{name}</b>'\n"))
    assert texts.get("hello", name="<script>") == "Привет, <b>&lt;script&gt;</b>"


def test_missing_substitution_is_error(tmp_path: Path) -> None:
    texts = load_texts(write_folder(tmp_path, "hello: 'Привет, {name}'\n"))
    with pytest.raises(ContentError, match="name"):
        texts.get("hello")


def test_required_text_missing_is_error(tmp_path: Path) -> None:
    texts = load_texts(write_folder(tmp_path, "menu:\n  title: Привет\n"))
    with pytest.raises(ContentError, match=r"menu\.about"):
        texts.require({"menu.title", "menu.about"})


def test_empty_text_is_error(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="пустой"):
        load_texts(write_folder(tmp_path, "menu:\n  title: '  '\n"))


def test_broken_yaml_is_error(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="YAML"):
        load_texts(write_folder(tmp_path, "menu: [\n"))


# --- Тексты по папкам: client/, admin/, alerts/ ---


def test_all_files_of_folder_make_one_set(tmp_path: Path) -> None:
    write(tmp_path / "client" / "menu.yaml", "menu:\n  title: Привет\n")
    write(tmp_path / "client" / "connect.yaml", "connect:\n  active: Готово\n")
    texts = load_texts(tmp_path / "client")
    assert texts.get("menu.title") == "Привет"
    assert texts.get("connect.active") == "Готово"


def test_one_section_can_be_split_across_files(tmp_path: Path) -> None:
    write(tmp_path / "admin" / "clients.yaml", "admin:\n  usage: Формат\n")
    write(tmp_path / "admin" / "stats.yaml", "admin:\n  stats:\n    title: Статистика\n")
    texts = load_texts(tmp_path / "admin")
    assert texts.get("admin.usage") == "Формат"
    assert texts.get("admin.stats.title") == "Статистика"


def test_admin_set_is_built_from_admin_and_alerts(tmp_path: Path) -> None:
    write(tmp_path / "admin" / "support.yaml", "support:\n  taken: Беру\n")
    write(tmp_path / "alerts" / "alerts.yaml", "alerts:\n  prefix: 🚨\n")
    texts = load_texts(tmp_path / "admin", tmp_path / "alerts")
    assert texts.get("support.taken") == "Беру"
    assert texts.get("alerts.prefix") == "🚨"


def test_same_key_in_two_files_stops_startup(tmp_path: Path) -> None:
    write(tmp_path / "admin" / "support.yaml", "support:\n  taken: Беру\n")
    write(tmp_path / "alerts" / "other.yaml", "support:\n  taken: Взял\n")
    with pytest.raises(ContentError) as error:
        load_texts(tmp_path / "admin", tmp_path / "alerts")
    message = str(error.value)
    assert "support.taken" in message
    assert "admin/support.yaml" in message
    assert "alerts/other.yaml" in message


def test_missing_text_error_names_folder(tmp_path: Path) -> None:
    texts = load_texts(write_folder(tmp_path, "menu:\n  title: Привет\n"))
    with pytest.raises(ContentError, match=r"client/.*menu\.about"):
        texts.get("menu.about")
    with pytest.raises(ContentError, match=r"client/.*menu\.about"):
        texts.require({"menu.about"})


def test_missing_folder_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="client"):
        load_texts(tmp_path / "client")


TARIFF = "tariffs:\n  - id: m1\n    title: 1 месяц\n    days: 30\n    price: {price}\n"


@pytest.mark.parametrize("price", ["150.5", "0", "-1", "abc", "true"])
def test_bad_price_is_error(tmp_path: Path, price: str) -> None:
    with pytest.raises(ContentError):
        load_tariffs(write(tmp_path / "tariffs.yaml", TARIFF.format(price=price)))


def test_string_price_with_kopecks_is_allowed(tmp_path: Path) -> None:
    tariffs = load_tariffs(write(tmp_path / "tariffs.yaml", TARIFF.format(price="'149.90'")))
    assert tariffs[0].price == Decimal("149.90")


def test_duplicate_tariff_id_is_error(tmp_path: Path) -> None:
    one = TARIFF.format(price=150).split("\n", 1)[1]
    with pytest.raises(ContentError, match="повторяются"):
        load_tariffs(write(tmp_path / "tariffs.yaml", "tariffs:\n" + one + one))


def test_hidden_field_is_gone(tmp_path: Path) -> None:
    """Задача 15: скрытых тарифов больше нет — поле `hidden` в файле — ошибка."""
    text = TARIFF.format(price=150) + "    hidden: true\n"
    with pytest.raises(ContentError, match="лишние"):
        load_tariffs(write(tmp_path / "tariffs.yaml", text))


def test_unknown_tariff_field_is_error(tmp_path: Path) -> None:
    text = TARIFF.format(price=150) + "    discount: 10\n"
    with pytest.raises(ContentError, match="лишние"):
        load_tariffs(write(tmp_path / "tariffs.yaml", text))


# --- Курс звезды (задача 33) ---


def test_repo_stars_rate_is_one_and_a_half_rubles() -> None:
    assert load_content(REPO_CONTENT, REPO_CONFIG).stars_rub_rate == Decimal("1.5")


@pytest.mark.parametrize(("raw", "rate"), [("1.5", "1.5"), ("2", "2"), ("'1.25'", "1.25")])
def test_stars_rate_is_read_exactly(tmp_path: Path, raw: str, rate: str) -> None:
    path = write(tmp_path / "tariffs.yaml", f"stars_rub_rate: {raw}\n")
    assert load_stars_rate(path) == Decimal(rate)


@pytest.mark.parametrize("raw", ["0", "-1.5", "abc", "true", "[]"])
def test_bad_stars_rate_stops_startup(tmp_path: Path, raw: str) -> None:
    path = write(tmp_path / "tariffs.yaml", f"stars_rub_rate: {raw}\n")
    with pytest.raises(ContentError, match="stars_rub_rate"):
        load_stars_rate(path)


def test_missing_stars_rate_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="stars_rub_rate"):
        load_stars_rate(write(tmp_path / "tariffs.yaml", TARIFF.format(price=150)))


# --- Быстрые ответы поддержки (задача 17) ---

QUICK = "quick_replies:\n  hello:\n    button: 👋\n    text: Здравствуйте!\n"


def test_groups_are_listed_in_file_order(tmp_path: Path) -> None:
    text = QUICK + "  done:\n    button: ✅\n    text: Готово\nother: x\n"
    texts = load_texts(write_folder(tmp_path, text))
    assert texts.ids_under("quick_replies") == ["hello", "done"]
    assert texts.ids_under("missing") == []


def test_repo_quick_replies_are_valid() -> None:
    admin_texts = load_content(REPO_CONTENT, REPO_CONFIG).admin_texts
    assert len(quick_reply_ids(admin_texts)) >= 3


def test_valid_quick_replies_pass(tmp_path: Path) -> None:
    assert quick_reply_ids(load_texts(write_folder(tmp_path, QUICK))) == ["hello"]


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("other: x\n", "хотя бы одна"),
        ("quick_replies:\n  hello:\n    button: 👋\n", r"quick_replies\.hello\.text"),
        ("quick_replies:\n  Привет:\n    button: 👋\n    text: Да\n", "латиница"),
        (QUICK.replace("hello", "h" * 33), "латиница"),
    ],
)
def test_broken_quick_replies_stop_startup(tmp_path: Path, text: str, error: str) -> None:
    with pytest.raises(ContentError, match=error):
        quick_reply_ids(load_texts(write_folder(tmp_path, text)))


def test_every_payment_status_has_admin_label() -> None:
    """Задача 15: тревога «статус оплаты изменился» называет любой статус по-человечески."""
    content = load_content(REPO_CONTENT, REPO_CONFIG)
    for status in PaymentStatus:
        assert content.admin_texts.get(f"payments.status.{status.value}")


# --- Описания тревог и видов сбоя (alerts/) ---

ALERT = (
    "alert:\n  panel_down:\n    title: Панель лежит\n    threat: клиенты без ссылок\n"
    "    where: клиент нажал кнопку\n"
)
FAILURE = (
    "failure:\n  panel:\n    timeout:\n      what: не дождались ответа\n      causes:\n"
    "        - cause: панель перезапускается\n          check: docker ps\n"
)


def test_list_of_texts_is_numbered_from_one(tmp_path: Path) -> None:
    texts = load_texts(write_folder(tmp_path, FAILURE))
    assert texts.get("failure.panel.timeout.causes.1.check") == "docker ps"


def test_complete_alert_and_failure_pass(tmp_path: Path) -> None:
    check_alert_texts(load_texts(write_folder(tmp_path, ALERT + FAILURE)), ["panel.timeout"])


@pytest.mark.parametrize("part", ["title", "threat", "where"])
def test_alert_without_part_stops_startup(tmp_path: Path, part: str) -> None:
    broken = "\n".join(line for line in ALERT.splitlines() if f"{part}:" not in line)
    with pytest.raises(ContentError, match=rf"alert\.panel_down\.{part}"):
        check_alert_texts(load_texts(write_folder(tmp_path, broken + "\n")), [])


def test_alert_with_own_causes_needs_cause_and_check(tmp_path: Path) -> None:
    complete = ALERT + "    causes:\n      - cause: нода перезагружается\n        check: uptime\n"
    check_alert_texts(load_texts(write_folder(tmp_path, complete)), [])
    broken = complete.replace("\n        check: uptime", "")
    with pytest.raises(ContentError, match=r"alert\.panel_down\.causes\.1\.check"):
        check_alert_texts(load_texts(write_folder(tmp_path, broken)), [])


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (
            FAILURE.replace("      what: не дождались ответа\n", ""),
            r"failure\.panel\.timeout\.what",
        ),
        (FAILURE.split("      causes:")[0], "причин"),
        (FAILURE.replace("\n          check: docker ps", ""), r"causes\.1\.check"),
    ],
)
def test_failure_without_text_or_causes_stops_startup(
    tmp_path: Path, text: str, error: str
) -> None:
    with pytest.raises(ContentError, match=error):
        check_alert_texts(load_texts(write_folder(tmp_path, text)), [])


def test_failure_kind_from_code_without_texts_stops_startup(tmp_path: Path) -> None:
    texts = load_texts(write_folder(tmp_path, FAILURE))
    with pytest.raises(ContentError, match=r"failure\.panel\.refused"):
        check_alert_texts(texts, ["panel.timeout", "panel.refused"])


def test_failure_kind_of_error_without_details_needs_only_error_text() -> None:
    """Ошибку создают и без подробностей (подделка панели в тестах) — тревога всё равно
    собирается: общий вид сбоя класса подставляет только {error}."""
    admin_texts = load_content(REPO_CONTENT, REPO_CONFIG).admin_texts
    errors = [
        cls
        for cls in vars(remnawave_types).values()
        if isinstance(cls, type) and issubclass(cls, ServiceError) and cls.default_kind
    ]
    assert errors
    for cls in errors:
        assert admin_texts.placeholders(f"failure.{cls.default_kind}.what") <= {"error"}
