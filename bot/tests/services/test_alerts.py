"""Тревоги: склейка одинаковых, сводка о повторах, маскировка, сбой отправки."""

import logging
from datetime import timedelta
from pathlib import Path

import pytest

from tsproxy.content.loader import Texts, load_content
from tsproxy.services.alerts import Alerter
from tsproxy.services.fakes import FakeAlertSender, FakeClock, SentMessage
from tsproxy.services.masking import Masker

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
REPO_CONFIG = Path(__file__).parent.parent.parent.parent / "config"
CHAT_ID = -100123
THREAD_ID = 2
WINDOW = timedelta(minutes=10)
SUBSCRIPTION_URL = "https://sub.ts-proxy.com/AbCdEfGh12345678"
SUBSCRIPTION_CODE_HEAD = "AbCdEfGh1234"


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


async def test_alert_goes_to_alerts_topic_with_prefix(
    alerter: Alerter, sender: FakeAlertSender
) -> None:
    await alerter.raise_alert("remnawave_down", "Remnawave не отвечает")
    assert sender.sent == [SentMessage(CHAT_ID, THREAD_ID, "🚨 Remnawave не отвечает")]


async def test_thousand_identical_alerts_give_one_message_and_counter(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    for _ in range(1000):
        await alerter.raise_alert("remnawave_down", "Remnawave не отвечает")
        clock.advance(timedelta(milliseconds=100))
    assert len(sender.sent) == 1

    clock.advance(WINDOW)
    await alerter.flush_repeats()

    assert len(sender.sent) == 2
    summary = sender.sent[1].text
    assert summary.startswith("🚨 ")
    assert "999" in summary
    assert "10 мин" in summary
    assert "Remnawave не отвечает" in summary


async def test_flush_before_window_end_sends_nothing(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    await alerter.raise_alert("k", "сбой")
    await alerter.raise_alert("k", "сбой")
    clock.advance(WINDOW - timedelta(seconds=1))
    await alerter.flush_repeats()
    assert len(sender.sent) == 1


async def test_window_without_repeats_closes_silently(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    await alerter.raise_alert("k", "сбой")
    clock.advance(WINDOW)
    await alerter.flush_repeats()
    await alerter.raise_alert("k", "сбой")
    assert [m.text for m in sender.sent] == ["🚨 сбой", "🚨 сбой"]


async def test_alert_after_window_sends_summary_then_new_alert(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    for _ in range(3):
        await alerter.raise_alert("k", "сбой")
    clock.advance(WINDOW)
    await alerter.raise_alert("k", "сбой снова")

    texts = [m.text for m in sender.sent]
    assert len(texts) == 3
    assert "2" in texts[1] and "сбой" in texts[1]
    assert texts[2] == "🚨 сбой снова"
    # сводка уже ушла — flush её не повторяет
    clock.advance(WINDOW)
    await alerter.flush_repeats()
    assert len(sender.sent) == 3


async def test_different_keys_are_not_glued(alerter: Alerter, sender: FakeAlertSender) -> None:
    await alerter.raise_alert("remnawave_down", "Remnawave не отвечает")
    await alerter.raise_alert("payments_down", "Платёжка не отвечает")
    await alerter.raise_alert("remnawave_down", "Remnawave не отвечает")
    assert len(sender.sent) == 2


async def test_alert_masks_secrets_and_subscription_urls(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock, bot_token: str
) -> None:
    text = f"сбой запроса bot{bot_token}, ссылка {SUBSCRIPTION_URL}"
    await alerter.raise_alert("k", text)
    await alerter.raise_alert("k", text)
    clock.advance(WINDOW)
    await alerter.flush_repeats()

    assert len(sender.sent) == 2
    for message in sender.sent:
        assert bot_token not in message.text
        assert SUBSCRIPTION_CODE_HEAD not in message.text
        assert "****5678" in message.text


async def test_alert_text_is_html_escaped(alerter: Alerter, sender: FakeAlertSender) -> None:
    await alerter.raise_alert("k", "<b>имя</b>")
    assert sender.sent[0].text == "🚨 &lt;b&gt;имя&lt;/b&gt;"


async def test_send_failure_is_logged_and_does_not_raise(
    alerter: Alerter, sender: FakeAlertSender, caplog: pytest.LogCaptureFixture
) -> None:
    sender.error = ConnectionError("Telegram недоступен")
    with caplog.at_level(logging.ERROR, logger="tsproxy.services.alerts"):
        await alerter.raise_alert("k", "сбой")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Не удалось отправить тревогу k" in errors[0].getMessage()
    assert errors[0].exc_info is not None


async def test_failed_send_still_glues_repeats(
    alerter: Alerter, sender: FakeAlertSender, clock: FakeClock
) -> None:
    sender.error = ConnectionError("Telegram недоступен")
    await alerter.raise_alert("k", "сбой")
    sender.error = None
    await alerter.raise_alert("k", "сбой")
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
