"""Настройки присмотра за бэкапами панели и темы отчёта (задача 22)."""

from pathlib import Path

from tsproxy.config import Settings

REQUIRED = {
    "bot_token": "1:token",
    "remnawave_token": "rw",
    "admin_ids": "1",
    "admin_chat_id": -100,
    "alerts_thread_id": 2,
    "backups_thread_id": 3,
}


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**REQUIRED, **overrides})  # type: ignore[call-arg, arg-type]


def test_defaults_point_to_panel_backup_script_paths() -> None:
    settings = make()
    assert settings.panel_backup_log == Path("/var/lib/remnawave-backup/backup.log")
    assert settings.panel_backup_dir == Path("/var/backups/remnawave/daily")
    assert settings.reports_thread_id is None


def test_empty_values_turn_panel_watch_off() -> None:
    settings = make(panel_backup_log="", panel_backup_dir="", reports_thread_id="")
    assert settings.panel_backup_log is None
    assert settings.panel_backup_dir is None
    assert settings.reports_thread_id is None
