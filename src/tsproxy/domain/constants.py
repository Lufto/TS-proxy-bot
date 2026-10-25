"""Числа из решений владельца — в одном месте.

Источники: задачи 04, 05, 12, 21 в `.scratch/vpn-bot-rework/issues/`
и `research/security-audit.md` §7.5–7.7.
"""

from datetime import timedelta
from decimal import Decimal

# --- Общие единицы ---
ONE_DAY = timedelta(days=1)
ONE_HOUR = timedelta(hours=1)
HOURS_PER_DAY = 24
BYTES_PER_GB = 1024**3

# --- Пробный период (задача 05) ---
TRIAL_DAYS = 7
TRIAL_TRAFFIC_GB = 7
TRIAL_DEVICE_LIMIT = 3
TRIAL_EXTENSION_DAYS = 3  # продление, если клиент ни разу не подключался
TRIAL_MAX_EXTENSIONS = 5  # итого до 7 + 5 × 3 = 22 дней
TRIAL_TRAFFIC_WARNING_PERCENT = 80  # предупредить, когда израсходовано 80% (ideas.md №1)
PERCENT = 100
# Автовыключение пробных (задача 28): больше 200 новых пробных за 10 минут — похоже
# на спам, бот выключает пробные сам; включает обратно только администратор
TRIAL_SPAM_LIMIT = 200
TRIAL_SPAM_WINDOW = timedelta(minutes=10)

# --- Платная подписка (задачи 05, 15) ---
PAID_DEVICE_LIMIT = 3
UNLIMITED_TRAFFIC_BYTES = 0  # в Remnawave 0 = без лимита трафика

# --- Льготный период (задача 05, задача 21 п. 11) ---
GRACE_DAYS = 3  # дни с ежедневным напоминанием
LAST_CHANCE_DAYS = 1  # ещё день с большим предупреждением, потом доступ выключается

# --- Тарифы и деньги (задача 04) ---
MONTH_DAYS = 30  # «месяц» в тарифах — ровно 30 дней
PRORATION_BONUS_HOURS = 2  # сверх пропорционального срока при оплате не на сумму тарифа
KOPECK = Decimal("0.01")
PAYMENT_LINK_LIFETIME = timedelta(hours=1)  # ссылка на оплату живёт час (правило 3)

# --- Выдача доступа после оплаты (задача 04 п. 6, задача 21 п. 7) ---
GRANT_FAST_RETRY_INTERVAL = timedelta(minutes=1)  # первые полчаса — каждую минуту
GRANT_FAST_RETRIES = 30  # ≈ 30 минут повторов раз в минуту, потом тревога
GRANT_SLOW_RETRY_INTERVAL = timedelta(minutes=10)  # дальше — раз в 10 минут
GRANT_RETRY_DEADLINE = timedelta(days=1)  # до суток с момента зачёта оплаты

# --- Возврат (задача 12) ---
REFUND_WINDOW_DAYS = 30  # возврат только в течение 30 дней с оплаты
REFUND_COOLDOWN_MONTHS = 6  # не чаще раза в полгода на клиента

# --- Админка (задача 18, аудит R22 §8) ---
# Предел ручной выдачи за раз: защита от «365 вместо 36» и лишнего нуля.
# Значение из аудита («например, 400 дней»), нужно подтверждение владельца.
MAX_MANUAL_GRANT_DAYS = 400

# --- Компенсация при сбое ноды (спецификация, админка п. 7; аудит §7.7) ---
COMPENSATION_BONUS_DAYS = 1  # простой + 1 день
# Падения ноды с перерывом меньше этого склеиваются в один инцидент («мигание»).
# Значение выбрано агентом, нужно подтверждение владельца.
OUTAGE_MERGE_GAP = timedelta(hours=1)
