"""Отметки фоновых задач (задача 22): когда каждая начала, успешно закончила или упала.

Отметки ставит планировщик (`app.PeriodicJobs`) вокруг каждого запуска, а читают
сторож (`services/watchdog.py`) и `/health`. Живут в памяти: после перезапуска бота
отсчёт идёт заново — от момента запуска.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from tsproxy.domain.monitoring import is_job_stale
from tsproxy.services.clock import Clock, utc_now


@dataclass(frozen=True)
class JobState:
    """Состояние одной задачи для сторожа и отчёта."""

    name: str
    stale_after: timedelta  # дольше без успеха — задача зависла
    watched_since: datetime  # с какого момента наблюдаем (запуск планировщика)
    last_success: datetime | None = None
    last_failure: datetime | None = None
    running_since: datetime | None = None  # выполняется прямо сейчас


class UnknownJobError(KeyError):
    """Отметка для задачи, которую не зарегистрировали, — ошибка сборки приложения."""


class JobHealth:
    """Отметки всех фоновых задач."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._jobs: dict[str, JobState] = {}

    def watch(self, name: str, stale_after: timedelta) -> None:
        """Начать наблюдать за задачей. Отсчёт «без успеха» — с этого момента."""
        if name in self._jobs:
            raise ValueError(f"Фоновая задача {name} уже зарегистрирована")
        if stale_after <= timedelta(0):
            raise ValueError("Срок «зависания» задачи должен быть больше нуля")
        self._jobs[name] = JobState(name, stale_after, watched_since=utc_now(self._clock))

    def started(self, name: str) -> None:
        self._update(name, running_since=utc_now(self._clock))

    def succeeded(self, name: str) -> None:
        self._update(name, running_since=None, last_success=utc_now(self._clock))

    def failed(self, name: str) -> None:
        self._update(name, running_since=None, last_failure=utc_now(self._clock))

    def states(self) -> list[JobState]:
        return list(self._jobs.values())

    def stale(self) -> list[JobState]:
        """Задачи, которые не отработали успешно дольше своего срока."""
        now = utc_now(self._clock)
        return [
            state
            for state in self._jobs.values()
            if is_job_stale(state.last_success, state.watched_since, state.stale_after, now)
        ]

    def _update(self, name: str, **changes: datetime | None) -> None:
        try:
            state = self._jobs[name]
        except KeyError:
            raise UnknownJobError(f"Фоновая задача {name} не зарегистрирована") from None
        self._jobs[name] = replace(state, **changes)
