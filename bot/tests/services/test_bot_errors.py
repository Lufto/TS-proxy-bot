"""«Ошибка в боте»: место в коде из трассировки, склейка по типу ошибки."""

from pathlib import Path

import pytest

from tsproxy.content.loader import ContentError, Texts, load_content
from tsproxy.services.alerts import Alerter
from tsproxy.services.bot_errors import alert_bot_error, code_place
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
REPO_CONFIG = Path(__file__).parent.parent.parent.parent / "config"


@pytest.fixture
def texts() -> Texts:
    return load_content(REPO_CONTENT, REPO_CONFIG).admin_texts


@pytest.fixture
def sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def alerter(sender: FakeAlertSender, texts: Texts, clock: FakeClock, mask: Masker) -> Alerter:
    return Alerter(sender, texts, -100123, 2, clock, mask)


def raised(error: Exception) -> Exception:
    try:
        raise error
    except Exception as caught:
        return caught


def test_place_is_the_last_frame_of_our_code() -> None:
    with pytest.raises(ContentError) as caught:
        Texts({}, "тест").get("нет_такого")
    place = code_place(caught.value)
    assert place is not None
    assert place.startswith("tsproxy.content.loader.Texts.get:")


async def test_error_outside_our_code_still_gives_full_alert(
    alerter: Alerter, sender: FakeAlertSender, texts: Texts
) -> None:
    """Трассировка только из кода библиотек (здесь — тестов): место — «нашего кода нет»."""
    error = raised(ValueError("сломалось в библиотеке"))
    assert code_place(error) is None

    await alert_bot_error(alerter, texts, error, texts.get("bot_error_action.job", name="отчёт"))

    [alert] = sender.sent
    assert "ValueError: сломалось в библиотеке" in alert.text
    assert texts.get("alert.bot_error.no_place") in alert.text
    assert "фоновая задача «отчёт»" in alert.text


async def test_errors_are_glued_by_type(
    alerter: Alerter, sender: FakeAlertSender, texts: Texts
) -> None:
    action = texts.get("bot_error_action.job", name="отчёт")
    for _ in range(3):
        await alert_bot_error(alerter, texts, raised(ValueError("a")), action)
    await alert_bot_error(alerter, texts, raised(KeyError("b")), action)

    assert ["ValueError" in m.text for m in sender.sent] == [True, False]
