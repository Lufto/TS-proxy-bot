"""Сборка приложения: льготный период подключён к вебхукам Remnawave и сверке (задача 11)."""

from pathlib import Path

from test_app import make_settings

from tsproxy.app import build_application, close_application
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.services.grace import GraceHandler
from tsproxy.services.term_guard import TermLockedHandlers


async def test_grace_handler_checks_every_account(tmp_path: Path, content: Content) -> None:
    app = build_application(
        make_settings(tmp_path), content, telegram_session=FakeTelegramSession()
    )
    await close_application(app)
    [job] = [j for j in app.jobs._jobs if j.name == "remnawave_reconcile"]
    # сверка и вебхуки идут через один и тот же AccountChecks
    [guarded] = job.run.__self__._accounts._handlers
    # льготный меняет срок — только под общей блокировкой срока (term_guard)
    assert isinstance(guarded, TermLockedHandlers)
    assert any(isinstance(handler, GraceHandler) for handler in guarded.handlers)
