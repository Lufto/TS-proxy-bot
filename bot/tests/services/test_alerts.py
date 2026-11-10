"""Тревоги: склейка одинаковых, сводка о повторах, маскировка, сбой отправки."""

import logging
from datetime import timedelta
from pathlib import Path

import pytest

from tsproxy.content.loader import Texts, load_content
from tsproxy.integrations.failures import Failure
from tsproxy.integrations.remnawave_types import RemnawaveFailure, RemnawaveUnavailable
from tsproxy.services.alerts import Alerter
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
REPO_CONFIG = Path(__file__).parent.parent.parent.parent / "config"
CHAT_ID = -100123
THREAD_ID = 2
WINDOW = timedelta(minutes=10)
SUBSCRIPTION_URL = "https://sub.ts-proxy.com/AbCdEfGh12345678"
SUBSCRIPTION_CODE_HEAD = "AbCdEfGh1234"
TELEGRAM_LIMIT = 4096
PANEL_502 = RemnawaveUnavailable(
    "Панель ответила HTTP 502",
    failure=Failure(RemnawaveFailure.SERVER_ERROR, {"status": 502, "attempts": 3}),
)


async def open_connect() -> None:
    """Место в коде, где «заметили» сбой, — для тревог в тестах."""


@pytest.fixture
def admin_texts() -> Texts:
    return load_content(REPO_CONTENT, REPO_CONFIG).admin_texts


@pytest.fixture
def sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def alerter(sender: FakeAlertSender, admin_texts: Texts, clock: FakeClock, mask: Masker) -> Alerter:
    return Alerter(
        sender=sender,
        texts=admin_texts,
        chat_id=CHAT_ID,
        thread_id=THREAD_ID,
        clock=clock,
        mask=mask,
        glue_window=WINDOW,
    )


async def fire(alerter: Alerter, glue: str = "k", **kwargs: object) -> None:
    """Тревога без вида сбоя («ошибок при сверке — N»): склейка по `glue`."""
    values: dict[str, object] = {"count": 1} | kwargs
    await alerter.alert("reconcile_failed", source=open_connect, glue=glue, **values)


async def test_alert_goes_to_alerts_topic_with_prefix(
    alerter: Alerter, sender: FakeAlertSender, admin_texts: Texts
) -> None:
    await fire(alerter)

    [message] = sender.sent
    assert (message.chat_id, message.thread_id) == (CHAT_ID, THREAD_ID)
    assert message.text.startswith("🚨 " + admin_texts.get("alert.reconcile_failed.title"))


async def test_thousand_identical_alerts_give_one_message_and_counter(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock, admin_texts: Texts
) -> None:
    for _ in range(1000):
        await fire(alerter)
        clock.advance(timedelta(milliseconds=100))
    assert len(sender.sent) == 1

    clock.advance(WINDOW)
    await alerter.flush_repeats()

    assert len(sender.sent) == 2
    summary = sender.sent[1].text
    assert summary.startswith("🚨 ")
    assert "999" in summary
    assert "10 мин" in summary
    assert admin_texts.get("alert.reconcile_failed.title") in summary


async def test_flush_before_window_end_sends_nothing(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    await fire(alerter)
    await fire(alerter)
    clock.advance(WINDOW - timedelta(seconds=1))
    await alerter.flush_repeats()
    assert len(sender.sent) == 1


async def test_window_without_repeats_closes_silently(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    await fire(alerter)
    clock.advance(WINDOW)
    await alerter.flush_repeats()
    await fire(alerter)
    [first, second] = [m.text for m in sender.sent]
    assert first == second


async def test_alert_after_window_sends_summary_then_new_alert(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock, admin_texts: Texts
) -> None:
    for _ in range(3):
        await fire(alerter)
    clock.advance(WINDOW)
    await fire(alerter, count=7)

    texts = [m.text for m in sender.sent]
    assert len(texts) == 3
    assert "Повторов за 10 мин после первой тревоги: 2" in texts[1]
    assert admin_texts.get("alert.reconcile_failed.what", count=7) in texts[2]
    # сводка уже ушла — flush её не повторяет
    clock.advance(WINDOW)
    await alerter.flush_repeats()
    assert len(sender.sent) == 3


async def test_different_keys_are_not_glued(alerter: Alerter, sender: FakeAlertSender) -> None:
    await fire(alerter, glue="remnawave_down")
    await fire(alerter, glue="payments_down")
    await fire(alerter, glue="remnawave_down")
    assert len(sender.sent) == 2


async def test_alert_masks_secrets_and_subscription_urls(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock, bot_token: str
) -> None:
    error = RuntimeError(f"сбой запроса bot{bot_token}, ссылка {SUBSCRIPTION_URL}")
    await fire(alerter, error=error)
    await fire(alerter, error=error)
    clock.advance(WINDOW)
    await alerter.flush_repeats()

    assert len(sender.sent) == 2
    for message in sender.sent:
        assert bot_token not in message.text
        assert SUBSCRIPTION_CODE_HEAD not in message.text
        assert "****5678" in message.text


async def test_alert_text_is_html_escaped(alerter: Alerter, sender: FakeAlertSender) -> None:
    await fire(alerter, error=RuntimeError("<b>имя</b>"))
    assert "&lt;b&gt;имя&lt;/b&gt;" in sender.sent[0].text
    assert "<b>имя" not in sender.sent[0].text


async def test_send_failure_is_logged_and_does_not_raise(
    alerter: Alerter, sender: FakeAlertSender, caplog: pytest.LogCaptureFixture
) -> None:
    sender.error = ConnectionError("Telegram недоступен")
    with caplog.at_level(logging.ERROR, logger="tsproxy.services.alerts"):
        await fire(alerter)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Не удалось отправить тревогу k" in errors[0].getMessage()
    assert errors[0].exc_info is not None


async def test_failed_send_still_glues_repeats(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    sender.error = ConnectionError("Telegram недоступен")
    await fire(alerter)
    sender.error = None
    await fire(alerter)
    assert sender.sent == []

    clock.advance(WINDOW)
    await alerter.flush_repeats()
    assert len(sender.sent) == 1
    assert "1" in sender.sent[0].text


def test_window_must_be_positive(
    sender: FakeAlertSender, admin_texts: Texts, clock: FakeClock, mask: Masker
) -> None:
    with pytest.raises(ValueError, match="больше нуля"):
        Alerter(sender, admin_texts, CHAT_ID, THREAD_ID, clock, mask, timedelta(0))


# --- Тревога из четырёх частей: что случилось, где заметили, чем грозит, куда смотреть ---


async def test_alert_tells_what_where_threat_and_causes(
    alerter: Alerter, sender: FakeAlertSender, admin_texts: Texts
) -> None:
    await alerter.alert("remnawave_unavailable", source=open_connect, error=PANEL_502)

    [message] = sender.sent
    text = message.text
    assert text.startswith("🚨 " + admin_texts.get("alert.remnawave_unavailable.title"))
    assert "HTTP 502" in text
    assert "попыток — 3" in text
    assert admin_texts.get("alert.remnawave_unavailable.where") in text
    assert f"{__name__}.open_connect" in text
    assert admin_texts.get("alert.remnawave_unavailable.threat") in text
    first = admin_texts.get("failure.remnawave.server_error.causes.1.cause")
    second = admin_texts.get("failure.remnawave.server_error.causes.2.cause")
    assert "1. " + first in text
    assert text.index(first) < text.index(second)


async def test_alert_masks_and_escapes_everything_from_error(
    alerter: Alerter, sender: FakeAlertSender, bot_token: str
) -> None:
    error = RemnawaveUnavailable(f"<b>bot{bot_token}</b> {SUBSCRIPTION_URL}")
    await alerter.alert("remnawave_unavailable", source=open_connect, error=error)

    text = sender.sent[0].text
    assert bot_token not in text
    assert SUBSCRIPTION_CODE_HEAD not in text
    assert "&lt;b&gt;" in text


async def test_huge_error_is_cut_but_keeps_every_part(
    alerter: Alerter, sender: FakeAlertSender, admin_texts: Texts
) -> None:
    error = RemnawaveUnavailable("<сбой> " * 5000)
    await alerter.alert("remnawave_unavailable", source=open_connect, error=error)

    text = sender.sent[0].text
    assert len(text) <= TELEGRAM_LIMIT
    assert "&lt;сбой&gt;" in text
    assert f"{__name__}.open_connect" in text
    assert admin_texts.get("alert.remnawave_unavailable.threat") in text
    last = admin_texts.ids_under("failure.remnawave.unavailable.causes")[-1]
    assert admin_texts.get(f"failure.remnawave.unavailable.causes.{last}.check") in text


async def test_new_alerts_are_glued_by_key(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    for _ in range(5):
        await alerter.alert("remnawave_unavailable", source=open_connect, error=PANEL_502)
    assert len(sender.sent) == 1

    clock.advance(WINDOW)
    await alerter.flush_repeats()

    [_, summary] = sender.sent
    assert "4" in summary.text
    assert "HTTP 502" in summary.text
    # готовый текст тревоги не экранируется второй раз
    assert "<code>" in summary.text
    assert "&lt;code" not in summary.text


async def test_alerts_from_different_places_glue_into_one(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock, admin_texts: Texts
) -> None:
    """Сверка и «Подключиться» видят один сбой панели — одна тревога (описание первой)
    и сводка повторов, а не по тревоге на каждое место."""
    await alerter.alert(
        "reconcile_unavailable",
        source=open_connect,
        error=PANEL_502,
        glue="remnawave_unavailable",
        part="ноды",
    )
    await alerter.alert("remnawave_unavailable", source=open_connect, error=PANEL_502)

    clock.advance(WINDOW)
    await alerter.flush_repeats()

    [first, summary] = sender.sent
    assert admin_texts.get("alert.reconcile_unavailable.where", part="ноды") in first.text
    assert "Повторов" in summary.text


async def test_alert_without_error_tells_own_what_and_causes(
    alerter: Alerter, sender: FakeAlertSender, admin_texts: Texts
) -> None:
    await alerter.alert("reconcile_failed", source=open_connect, count=3)

    [alert] = sender.sent
    assert admin_texts.get("alert.reconcile_failed.what", count=3) in alert.text
    assert admin_texts.get("alert_format.causes") in alert.text
    assert admin_texts.get("alert.reconcile_failed.causes.2.cause") in alert.text


async def test_alert_without_error_and_causes_has_no_causes_part(
    alerter: Alerter, sender: FakeAlertSender, admin_texts: Texts
) -> None:
    await alerter.alert(
        "node_restored", source=open_connect, name="Нода", minutes=5, since="12:00", until="12:05"
    )

    [alert] = sender.sent
    assert admin_texts.get("alert.node_restored.title", name="Нода") in alert.text
    assert "простой — 5 мин" in alert.text
    assert admin_texts.get("alert_format.causes") not in alert.text


# --- Сводка повторов по причинам: сколько раз каждое «что случилось» за окно ---

PANEL_TIMEOUT = RemnawaveUnavailable(
    "Панель не ответила вовремя",
    failure=Failure(RemnawaveFailure.TIMEOUT, {"seconds": 10, "attempts": 3}),
)


async def test_summary_counts_each_cause_of_repeats(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    for _ in range(15):
        await alerter.alert("remnawave_unavailable", source=open_connect, error=PANEL_502)
    for _ in range(3):
        await alerter.alert("remnawave_unavailable", source=open_connect, error=PANEL_TIMEOUT)
    assert len(sender.sent) == 1

    clock.advance(WINDOW)
    await alerter.flush_repeats()

    [_, summary] = sender.sent
    assert "Повторов за 10 мин после первой тревоги: 17" in summary.text
    assert "×14 — панель ответила HTTP 502, попыток — 3" in summary.text
    assert "×3 — не дождались ответа панели за 10 сек, попыток — 3" in summary.text
    assert summary.button is None


async def test_summary_of_one_cause_has_no_breakdown(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    for _ in range(5):
        await alerter.alert("remnawave_unavailable", source=open_connect, error=PANEL_502)
    clock.advance(WINDOW)
    await alerter.flush_repeats()

    [first, summary] = sender.sent
    assert summary.text == (
        "🚨 Повторов за 10 мин после первой тревоги: 4\n" + first.text.removeprefix("🚨 ")
    )


async def test_summary_of_many_causes_fits_telegram_limit(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    huge = RemnawaveUnavailable("<сбой> " * 5000)
    await alerter.alert("remnawave_unavailable", source=open_connect, error=huge)
    for number in range(300):
        error = RemnawaveUnavailable(f"сбой №{number} " + "<длинный текст> " * 50)
        await alerter.alert("remnawave_unavailable", source=open_connect, error=error)
    clock.advance(WINDOW)
    await alerter.flush_repeats()

    [_, summary] = sender.sent
    assert len(summary.text) <= TELEGRAM_LIMIT
    assert "Повторов за 10 мин после первой тревоги: 300" in summary.text
    assert "×1 — " in summary.text
