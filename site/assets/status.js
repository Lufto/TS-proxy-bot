// Плашка «Возможны неполадки» (задача 25).
// Раз в минуту спрашиваем у бота /health: ответ 200 — всё хорошо, плашку прячем;
// другой ответ или ошибка сети — показываем. Без cookies, библиотек и счётчиков.
// Бот отдаёт на /health заголовок Access-Control-Allow-Origin, иначе браузер не даст прочитать ответ.

// TODO(владелец): адрес проверки бота — меняется только здесь
const HEALTH_URL = "https://bot.ts-proxy.com/health";
const CHECK_EVERY_MS = 60 * 1000;
const TIMEOUT_MS = 10 * 1000;

const banner = document.getElementById("status-banner");

async function isHealthy() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(HEALTH_URL, {
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
    });
    return response.status === 200;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function check() {
  // нет интернета у самого посетителя — это не наша неполадка, плашку не показываем
  if (!navigator.onLine) {
    banner.hidden = true;
    return;
  }
  banner.hidden = await isHealthy();
}

if (banner) {
  check();
  setInterval(() => {
    // вкладка в фоне — не дёргаем бота зря, проверим, когда вернутся
    if (!document.hidden) {
      check();
    }
  }, CHECK_EVERY_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      check();
    }
  });
}
