"""Порог автовыключения пробных (задача 28): больше 200 новых пробных за 10 минут."""

from datetime import UTC, datetime, timedelta

from tsproxy.domain.constants import TRIAL_SPAM_LIMIT, TRIAL_SPAM_WINDOW
from tsproxy.domain.trial import recent_trials, trial_spam_detected

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def test_two_hundred_trials_are_not_spam() -> None:
    assert TRIAL_SPAM_LIMIT == 200
    assert not trial_spam_detected(200)


def test_two_hundred_and_first_trial_is_spam() -> None:
    assert trial_spam_detected(201)


def test_window_is_ten_minutes() -> None:
    assert timedelta(minutes=10) == TRIAL_SPAM_WINDOW


def test_recent_trials_keep_only_last_ten_minutes() -> None:
    old = NOW - TRIAL_SPAM_WINDOW
    fresh = NOW - TRIAL_SPAM_WINDOW + timedelta(seconds=1)

    assert recent_trials([old, fresh, NOW], NOW) == [fresh, NOW]
