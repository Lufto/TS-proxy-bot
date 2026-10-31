from decimal import Decimal
from pathlib import Path

import pytest

from tsproxy.content.loader import (
    ContentError,
    load_content,
    load_stars_rate,
    load_tariffs,
    load_texts,
    quick_reply_ids,
)

REPO_CONTENT = Path(__file__).parent.parent / "content"


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_content_loads() -> None:
    content = load_content(REPO_CONTENT)
    assert content.tariff("m3") is not None
    assert content.tariff("m3").price == Decimal("405")
    assert [t.id for t in content.tariffs if t.hidden] == ["test15"]


def test_texts_are_flattened_by_screen(tmp_path: Path) -> None:
    texts = load_texts(write(tmp_path / "t.yaml", "menu:\n  title: Привет\n"))
    assert texts.get("menu.title") == "Привет"


def test_substitutions_are_html_escaped(tmp_path: Path) -> None:
    texts = load_texts(write(tmp_path / "t.yaml", "hello: 'Привет, <b>{name}</b>'\n"))
    assert texts.get("hello", name="<script>") == "Привет, <b>&lt;script&gt;</b>"


def test_missing_substitution_is_error(tmp_path: Path) -> None:
    texts = load_texts(write(tmp_path / "t.yaml", "hello: 'Привет, {name}'\n"))
    with pytest.raises(ContentError, match="name"):
        texts.get("hello")


def test_required_text_missing_is_error(tmp_path: Path) -> None:
    texts = load_texts(write(tmp_path / "t.yaml", "menu:\n  title: Привет\n"))
    with pytest.raises(ContentError, match=r"menu\.about"):
        texts.require({"menu.title", "menu.about"})


def test_empty_text_is_error(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="пустой"):
        load_texts(write(tmp_path / "t.yaml", "menu:\n  title: '  '\n"))


def test_broken_yaml_is_error(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="YAML"):
        load_texts(write(tmp_path / "t.yaml", "menu: [\n"))


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


def test_only_hidden_tariffs_is_error(tmp_path: Path) -> None:
    text = TARIFF.format(price=150) + "    hidden: true\n"
    with pytest.raises(ContentError, match="для клиентов"):
        load_tariffs(write(tmp_path / "tariffs.yaml", text))


def test_unknown_tariff_field_is_error(tmp_path: Path) -> None:
    text = TARIFF.format(price=150) + "    discount: 10\n"
    with pytest.raises(ContentError, match="лишние"):
        load_tariffs(write(tmp_path / "tariffs.yaml", text))


# --- Курс звезды (задача 33) ---


def test_repo_stars_rate_is_one_and_a_half_rubles() -> None:
    assert load_content(REPO_CONTENT).stars_rub_rate == Decimal("1.5")


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
    texts = load_texts(write(tmp_path / "t.yaml", text))
    assert texts.ids_under("quick_replies") == ["hello", "done"]
    assert texts.ids_under("missing") == []


def test_repo_quick_replies_are_valid() -> None:
    admin_texts = load_content(REPO_CONTENT).admin_texts
    assert len(quick_reply_ids(admin_texts)) >= 3


def test_valid_quick_replies_pass(tmp_path: Path) -> None:
    assert quick_reply_ids(load_texts(write(tmp_path / "t.yaml", QUICK))) == ["hello"]


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
        quick_reply_ids(load_texts(write(tmp_path / "t.yaml", text)))
