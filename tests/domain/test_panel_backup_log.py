from datetime import UTC, datetime, timedelta

from tsproxy.domain.panel_backup import (
    PANEL_BACKUP_MAX_AGE,
    PanelBackupLog,
    PanelBackupProblem,
    panel_backup_problems,
    parse_backup_log,
)

COPY = "/var/backups/remnawave/daily/remnawave-2026-10-24.tar.gz.age"
OK_LINE = f"2026-10-24T01:00:03+00:00 OK: {COPY} (208K)"
NOT_SENT_LINE = "2026-10-24T01:02:03+00:00 telegram: файл не отправлен"
FAIL_LINE = "2026-10-25T01:00:09+00:00 FAIL: строка 57"
NOW = datetime(2026, 10, 25, 7, 0, tzinfo=UTC)


def test_empty_log() -> None:
    assert parse_backup_log([]) == PanelBackupLog(None, None, None, sent=True)


def test_last_ok_copy_is_found() -> None:
    log = parse_backup_log([OK_LINE, "2026-10-24T01:00:04+00:00 чистка пропущена: …"])
    assert log.last_ok_at == datetime(2026, 10, 24, 1, 0, 3, tzinfo=UTC)
    assert log.last_ok_copy == COPY
    assert log.sent


def test_file_not_sent_after_last_ok() -> None:
    assert not parse_backup_log([OK_LINE, NOT_SENT_LINE]).sent


def test_old_not_sent_is_forgotten_after_new_ok() -> None:
    newer_ok = OK_LINE.replace("2026-10-24T01", "2026-10-25T01")
    assert parse_backup_log([OK_LINE, NOT_SENT_LINE, newer_ok]).sent


def test_failed_message_is_not_a_failed_copy() -> None:
    """«сообщение не отправлено» — про тревогу скрипта, а не про файл копии."""
    lines = [OK_LINE, "2026-10-24T01:02:03+00:00 telegram: сообщение не отправлено"]
    assert parse_backup_log(lines).sent


def test_last_failure_is_kept() -> None:
    log = parse_backup_log([OK_LINE, FAIL_LINE])
    assert log.last_failure_at == datetime(2026, 10, 25, 1, 0, 9, tzinfo=UTC)


def test_broken_and_partial_lines_are_skipped() -> None:
    """Журнал читается с хвоста: первая строка может быть обрезана."""
    log = parse_backup_log(["0-24T01:00:03+00:00 OK: обрывок", "", "мусор", OK_LINE])
    assert log.last_ok_copy == COPY


def test_fresh_and_sent_copy_has_no_problems() -> None:
    log = parse_backup_log([OK_LINE])
    newest = NOW - timedelta(hours=6)
    assert panel_backup_problems(log, newest, NOW) == frozenset()


def test_no_copy_at_all_is_stale() -> None:
    log = parse_backup_log([])
    assert panel_backup_problems(log, None, NOW) == {PanelBackupProblem.STALE}


def test_copy_older_than_26_hours_is_stale() -> None:
    log = parse_backup_log([OK_LINE])
    newest = NOW - PANEL_BACKUP_MAX_AGE - timedelta(minutes=1)
    assert panel_backup_problems(log, newest, NOW) == {PanelBackupProblem.STALE}


def test_copy_exactly_26_hours_old_is_still_fine() -> None:
    log = parse_backup_log([OK_LINE])
    assert panel_backup_problems(log, NOW - PANEL_BACKUP_MAX_AGE, NOW) == frozenset()


def test_copy_not_sent_to_telegram() -> None:
    log = parse_backup_log([OK_LINE, NOT_SENT_LINE])
    newest = NOW - timedelta(hours=6)
    assert panel_backup_problems(log, newest, NOW) == {PanelBackupProblem.NOT_SENT}
