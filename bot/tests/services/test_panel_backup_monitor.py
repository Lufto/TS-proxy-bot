"""Присмотр за бэкапами панели: нет свежей копии или копия не ушла в Telegram — тревога."""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import NOW
from signal_helpers import admin_texts, make_alerter

from tsproxy.domain.panel_backup import PanelBackupProblem
from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.panel_backups import PanelBackupMonitor, PanelBackupUnreadableError


@pytest.fixture
def copies(tmp_path: Path) -> Path:
    path = tmp_path / "daily"
    path.mkdir()
    return path


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "backup.log"


@pytest.fixture
def sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def monitor(
    log_path: Path, copies: Path, sender: FakeAlertSender, clock: FakeClock, mask: Masker
) -> PanelBackupMonitor:
    return PanelBackupMonitor(
        log_path, copies, make_alerter(sender, clock, mask), admin_texts(), clock
    )


def make_copy(copies: Path, made_at: datetime, *, day: str = "2026-10-24") -> Path:
    path = copies / f"remnawave-{day}.tar.gz.age"
    path.write_bytes(b"x" * 2048)
    os.utime(path, (made_at.timestamp(), made_at.timestamp()))
    return path


def write_log(log_path: Path, *lines: str) -> None:
    log_path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def ok_line(copy: Path, at: datetime) -> str:
    return f"{at.isoformat(timespec='seconds')} OK: {copy} (2.0K)"


async def test_fresh_sent_copy_is_fine(
    monitor: PanelBackupMonitor, copies: Path, log_path: Path, sender: FakeAlertSender
) -> None:
    made_at = NOW - timedelta(hours=8)
    copy = make_copy(copies, made_at)
    write_log(log_path, ok_line(copy, made_at))

    status = await monitor.status()
    await monitor.check()

    assert status.problems == frozenset()
    assert status.newest is not None
    assert status.newest.size_bytes == 2048
    assert sender.sent == []


async def test_stale_copy_alerts_once(
    monitor: PanelBackupMonitor,
    copies: Path,
    log_path: Path,
    sender: FakeAlertSender,
    clock: FakeClock,
) -> None:
    made_at = NOW - timedelta(hours=27)
    copy = make_copy(copies, made_at, day="2026-10-23")
    write_log(log_path, ok_line(copy, made_at))

    for _ in range(5):
        await monitor.check()
        clock.advance(timedelta(hours=1))

    [alert] = sender.sent
    assert "26 часов" in alert.text
    assert "23.10.2026" in alert.text
    assert "PanelBackupMonitor.check" in alert.text
    assert admin_texts().get("alert.panel_backup_stale.causes.3.cause") in alert.text


async def test_no_copies_and_no_log_is_stale(
    monitor: PanelBackupMonitor, sender: FakeAlertSender
) -> None:
    status = await monitor.status()
    await monitor.check()
    assert status.problems == {PanelBackupProblem.STALE}
    assert "ни одной копии" in sender.sent[0].text


async def test_missing_copies_dir_is_stale(
    tmp_path: Path, log_path: Path, clock: FakeClock, mask: Masker
) -> None:
    monitor = PanelBackupMonitor(
        log_path,
        tmp_path / "nope",
        make_alerter(FakeAlertSender(), clock, mask),
        admin_texts(),
        clock,
    )
    assert (await monitor.status()).problems == {PanelBackupProblem.STALE}


async def test_copy_not_sent_to_telegram_alerts(
    monitor: PanelBackupMonitor, copies: Path, log_path: Path, sender: FakeAlertSender
) -> None:
    made_at = NOW - timedelta(hours=8)
    copy = make_copy(copies, made_at)
    write_log(
        log_path,
        ok_line(copy, made_at),
        f"{made_at.isoformat(timespec='seconds')} telegram: файл не отправлен",
    )

    await monitor.check()
    await monitor.check()

    [alert] = sender.sent
    assert "в Telegram не ушла" in alert.text
    assert str(copy) in alert.text


async def test_next_unsent_copy_alerts_again(
    monitor: PanelBackupMonitor,
    copies: Path,
    log_path: Path,
    sender: FakeAlertSender,
    clock: FakeClock,
) -> None:
    first_at = NOW - timedelta(hours=8)
    first = make_copy(copies, first_at)
    not_sent = "telegram: файл не отправлен"
    write_log(log_path, ok_line(first, first_at), f"{first_at.isoformat()} {not_sent}")
    await monitor.check()

    clock.advance(timedelta(days=1))
    second_at = first_at + timedelta(days=1)
    second = make_copy(copies, second_at, day="2026-10-25")
    write_log(
        log_path,
        ok_line(first, first_at),
        f"{first_at.isoformat()} {not_sent}",
        ok_line(second, second_at),
        f"{second_at.isoformat()} {not_sent}",
    )
    await monitor.check()

    assert len(sender.sent) == 2
    assert str(second) in sender.sent[1].text


@pytest.mark.skipif(os.geteuid() == 0, reason="root читает всё, права не проверить")
async def test_unreadable_dir_alerts(
    monitor: PanelBackupMonitor, copies: Path, sender: FakeAlertSender
) -> None:
    os.chmod(copies, 0o000)
    try:
        with pytest.raises(PanelBackupUnreadableError):
            await monitor.status()
        await monitor.check()
        await monitor.check()
    finally:
        os.chmod(copies, 0o700)

    [alert] = sender.sent
    texts = admin_texts()
    assert texts.get("alert.panel_backup_unreadable.title") in alert.text
    assert "Что случилось: PermissionError" in alert.text  # без обёртки бота
    assert "PanelBackupMonitor.check" in alert.text
    assert texts.get("alert.panel_backup_unreadable.causes.1.cause") in alert.text
