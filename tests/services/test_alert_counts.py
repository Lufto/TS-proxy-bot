"""Счёт тревог по дням (по Москве) — для ежедневного отчёта."""

from datetime import date, timedelta

from conftest import NOW
from signal_helpers import make_alerter

from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker


async def test_alerts_are_counted_with_glued_repeats(clock: FakeClock, mask: Masker) -> None:
    alerter = make_alerter(FakeAlertSender(), clock, mask)
    for _ in range(3):
        await alerter.raise_alert("same", "одно и то же")
    await alerter.raise_alert("other", "другое")

    assert alerter.raised_on(date(2026, 10, 24)) == 4
    assert alerter.counting_since == NOW


async def test_count_follows_moscow_date(clock: FakeClock, mask: Masker) -> None:
    alerter = make_alerter(FakeAlertSender(), clock, mask)
    clock.now = NOW.replace(hour=21, minute=30)  # 00:30 МСК 25.10
    await alerter.raise_alert("night", "ночью")

    assert alerter.raised_on(date(2026, 10, 24)) == 0
    assert alerter.raised_on(date(2026, 10, 25)) == 1


async def test_old_days_are_forgotten(clock: FakeClock, mask: Masker) -> None:
    alerter = make_alerter(FakeAlertSender(), clock, mask)
    await alerter.raise_alert("a", "давно")
    clock.advance(timedelta(days=30))
    await alerter.raise_alert("a", "сейчас")

    assert alerter.raised_on(date(2026, 10, 24)) == 0
