"""Сторож фоновых задач (задача 18 в `vpn-bot-rework`, задача 22).

Раз в минуту смотрит отметки `JobHealth`: задача, которая не отработала успешно дольше
своего срока (зависла, падает каждый раз или не запускается), — тревога в «🚨 Тревоги».
Тревога — один раз на эпизод: пока задача стоит, сторож молчит; когда снова отработала —
сообщение «снова работает». Самого сторожа сторожит `/health` (и UptimeRobot снаружи):
он тоже фоновая задача со своими отметками.
"""

import logging

from tsproxy.content.loader import Texts
from tsproxy.domain.dates import format_moscow
from tsproxy.services.alerts import Alerter
from tsproxy.services.job_health import JobHealth, JobState

logger = logging.getLogger(__name__)

JOB_STALE_ALERT = "job_stale"
JOB_STALE_NEVER_ALERT = "job_stale_never"  # ни разу не отработала с запуска бота
# Отдельный ключ: «ожила» сразу после «зависла» не должна склеиться с ней в повтор
JOB_RECOVERED_ALERT = "job_recovered"


class JobWatchdog:
    """Поднимает тревогу о зависших фоновых задачах и сообщает, когда они ожили."""

    def __init__(self, health: JobHealth, alerter: Alerter, admin_texts: Texts) -> None:
        self._health = health
        self._alerter = alerter
        self._texts = admin_texts
        self._alerted: set[str] = set()  # о каких задачах уже сказали в этом эпизоде

    async def run(self) -> None:
        stale = {state.name: state for state in self._health.stale()}
        for name in sorted(stale.keys() - self._alerted):
            logger.error("Фоновая задача %s зависла или падает", name)
            await self._alert_stale(stale[name])
        for name in sorted(self._alerted - stale.keys()):
            logger.info("Фоновая задача %s снова работает", name)
            await self._alerter.alert(
                JOB_RECOVERED_ALERT,
                source=self.run,
                glue=f"{JOB_RECOVERED_ALERT}:{name}",
                name=name,
            )
        self._alerted = set(stale)

    async def _alert_stale(self, state: JobState) -> None:
        if state.running_since is not None:
            hint = self._texts.get(
                "alert.job_stale.hint_running", since=format_moscow(state.running_since)
            )
        else:
            hint = self._texts.get("alert.job_stale.hint_failing")
        never = state.last_success is None
        since = state.watched_since if state.last_success is None else state.last_success
        await self._alerter.alert(
            JOB_STALE_NEVER_ALERT if never else JOB_STALE_ALERT,
            source=self.run,
            glue=f"{JOB_STALE_ALERT}:{state.name}",
            name=state.name,
            since=format_moscow(since),
            hint=hint,
        )
