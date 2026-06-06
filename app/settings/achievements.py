"""Каталог достижений Возни (категории, легендарные и секретные).

==============================================================================
ЭТОТ КАТАЛОГ МОЖНО РАСШИРЯТЬ.
Поля достижения: код, эмодзи, название, описание, категория, метрика,
порог и награда (в ешках, 0 — без награды). hidden=True — секретное.

Категории: economy, casino, duel, treasure, marriage, nomination, legend, secret.

Метрики (по чему считается достижение):
  total_earned         — всего заработано (продуктивно, без гэмблинга)
  farm_success_count   — успешных ферм
  casino_games_count   — сыграно игр в казино
  duels_won            — побед в дуэлях
  treasures_found      — найдено кладов
  marriages_count      — заключено браков
  pidor_count          — сколько раз был «Пидором дня»
  max_farm_streak      — рекорд серии фермы
  max_casino_loss      — крупнейший проигрыш в казино (секретки)
  casino_loss_streak   — текущая серия проигрышей в казино (секретки)
  duel_loss_streak     — текущая серия поражений в дуэлях (секретки)
  all                  — открыты все «основные» достижения (особая метрика)
  event                — выдаётся событием в коде (джекпот, быстрый клад и т.п.)

Награды откалиброваны под доход ~45 ешек/день (см. ECONOMY.md): ранние ачивки
дают быстрый буст, легенды — крупные, но редкие; всё ≈ за месяцы игры.
==============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass

METRIC_ALL = "all"
METRIC_EVENT = "event"

# Категории и их подписи (порядок отображения в /ачивки).
CATEGORY_ORDER = [
    ("economy", "💰 Экономика"),
    ("casino", "🎰 Казино"),
    ("duel", "⚔️ Дуэли"),
    ("treasure", "📦 Клады"),
    ("marriage", "💍 Браки"),
    ("nomination", "🏳️ Номинации"),
    ("legend", "👑 Легенды Возни"),
]
SECRET_CATEGORY = "secret"


@dataclass(frozen=True)
class Achievement:
    """Одно достижение."""

    code: str
    emoji: str
    name: str
    description: str
    category: str
    metric: str
    threshold: int = 0
    reward: int = 0
    hidden: bool = False

    @property
    def label(self) -> str:
        """Эмодзи + название."""
        return f"{self.emoji} {self.name}"


ACHIEVEMENTS: list[Achievement] = [
    # --- 💰 Экономика -------------------------------------------------------
    Achievement("first_ezhka", "🌱", "Первая ешка", "Заработал первую ешку",
                "economy", "total_earned", 1, reward=10),
    Achievement("thousandaire", "💰", "Первый чемодан", "Поднял 1 000 ешек за всё время",
                "economy", "total_earned", 1000, reward=100),
    Achievement("magnate", "💰", "Магнат аптеки", "Поднял 10 000 ешек за всё время",
                "economy", "total_earned", 10000, reward=400),

    # --- 🎰 Казино ----------------------------------------------------------
    Achievement("ludoman", "🎰", "Лудоман", "Крутанул казино 10 раз",
                "casino", "casino_games_count", 10, reward=50),
    Achievement("casino_grandpa", "🎰", "Казиношный дед", "Крутанул казино 100 раз",
                "casino", "casino_games_count", 100, reward=150),

    # --- ⚔️ Дуэли -----------------------------------------------------------
    Achievement("duelist", "⚔️", "Дуэлянт", "Забрал первую дуэль",
                "duel", "duels_won", 1, reward=50),
    Achievement("gladiator", "⚔️", "Возняшный боец", "Выиграл 25 дуэлей",
                "duel", "duels_won", 25, reward=200),

    # --- 📦 Клады -----------------------------------------------------------
    Achievement("treasure_hunter", "📦", "Кладоискатель", "Поднял первый клад",
                "treasure", "treasures_found", 1, reward=50),
    Achievement("treasure_master", "📦", "Охотник за чемоданами", "Поднял 10 кладов",
                "treasure", "treasures_found", 10, reward=200),

    # --- 💍 Браки -----------------------------------------------------------
    Achievement("true_love", "💍", "Любовь существует", "Сыграл первую свадьбу",
                "marriage", "marriages_count", 1, reward=50),
    Achievement("serial_groom", "💍", "Серийный жених", "Сыграл 5 свадеб",
                "marriage", "marriages_count", 5, reward=200),

    # --- 🏳️ Номинации -------------------------------------------------------
    Achievement("nominee", "🏳️", "Звезда дня", "Стал «Пидором дня» 1 раз",
                "nomination", "pidor_count", 1, reward=25),
    Achievement("nominee_regular", "🏳️", "Завсегдатай номинаций",
                "Стал «Пидором дня» 10 раз", "nomination", "pidor_count", 10, reward=150),

    # --- 👑 Легенды Возни ---------------------------------------------------
    Achievement("apteka_magnate", "💊", "Аптечный магнат", "500 удачных ферм",
                "legend", "farm_success_count", 500, reward=400),
    Achievement("already_red", "🔥", "Уже красный", "Серия фермы 30 дней",
                "legend", "max_farm_streak", 30, reward=300),
    Achievement("unburnable", "🌾", "Несгораемый", "Серия фермы 60 дней",
                "legend", "max_farm_streak", 60, reward=600),
    Achievement("voznya_started", "⚔️", "Возня началась", "100 побед в дуэлях",
                "legend", "duels_won", 100, reward=500),
    Achievement("war_machine", "⚔️", "Машина возни", "250 побед в дуэлях",
                "legend", "duels_won", 250, reward=900),
    Achievement("cursed_suitcase", "📦", "Ёбаный чемодан", "Поднял 50 кладов",
                "legend", "treasures_found", 50, reward=500),
    Achievement("radik_vault", "📦", "Кладовая Радика", "Поднял 100 кладов",
                "legend", "treasures_found", 100, reward=900),
    Achievement("nomination_king", "🏳️", "Король номинаций", "50 раз «Пидор дня»",
                "legend", "pidor_count", 50, reward=500),
    # Переименовано из «Авторитет» во избежание путаницы с рангом MMR.
    Achievement("authority", "☢️", "Аптечный авторитет", "Поднял 25 000 ешек за всё время",
                "legend", "total_earned", 25000, reward=750),
    Achievement("suitcase_man", "🧳", "Чемоданщик", "Поднял 50 000 ешек за всё время",
                "legend", "total_earned", 50000, reward=1200),
    Achievement("overdose", "💉", "Аптечный передоз", "Поднял 100 000 ешек за всё время",
                "legend", "total_earned", 100000, reward=2000),
    Achievement("absolute_ludik", "🎰", "Абсолютный лудик", "Крутанул казино 500 раз",
                "legend", "casino_games_count", 500, reward=700),
    Achievement("catushka", "🎰", "Пошла катушка", "Сорвал джекпот в казино",
                "legend", METRIC_EVENT, reward=250),
    Achievement("last_dep", "🍺", "Последний деп", "Поставил всё в казино и слил",
                "legend", METRIC_EVENT, reward=50),
    Achievement("love_grave", "💍", "Любовь до гроба", "Прожил в браке 30 дней",
                "legend", METRIC_EVENT, reward=250),
    Achievement("mellstroy", "👑", "Меллстрой Возни", "Открыть все основные достижения",
                "legend", METRIC_ALL, reward=1500),

    # --- 🤫 Секретные (скрыты до открытия) ----------------------------------
    Achievement("ludik_secret", "🎰", "Лудик", "Слил крупную сумму в казино",
                SECRET_CATEGORY, "max_casino_loss", 500, reward=0, hidden=True),
    Achievement("no_luck", "💀", "Не фартануло", "Серия из 5 проигрышей в казино",
                SECRET_CATEGORY, "casino_loss_streak", 5, reward=0, hidden=True),
    Achievement("bag", "⚔️", "Мешок", "Серия из 5 поражений в дуэлях",
                SECRET_CATEGORY, "duel_loss_streak", 5, reward=0, hidden=True),
    Achievement("kladmen", "📦", "Кладмен", "Забрал клад почти мгновенно",
                SECRET_CATEGORY, METRIC_EVENT, reward=100, hidden=True),
    Achievement("ghost", "👻", "Призрак Возни", "Вернулся после долгого отсутствия",
                SECRET_CATEGORY, METRIC_EVENT, reward=50, hidden=True),
]

# ============================================================================
# БУДУЩИЕ ДОСТИЖЕНИЯ — ТРЕБУЮТ НОВЫХ СЧЁТЧИКОВ (пока НЕ добавлены).
#
# Эти достижения вписываются в мир, но им нужна метрика, которой ещё нет в
# сборщике статистики (app/features/achievements/service.py). Добавлять вместе
# с реализацией соответствующей метрики и тестом — по одной за раз:
#
#   • mmr (новая категория): «Путь начался» (MMR≥1000), «Свой в Зволле»
#     (MMR≥2500), «Котость подтверждена» (MMR≥5000), «Вершина Возни»
#     (MMR≥25000). Метрика: текущий MMR = SUM(mmr_entries.amount).
#   • reputation (новая категория): «Конинг заметил» (rep≥10), «Уважение двора»
#     (rep≥50), «Тёмный друн» (rep≤−10, секрет). Метрика: SUM(reputation_entries.value).
#   • legacy (новая категория): «Старожил Возни» (10k сообщений), «Древний друн»
#     (50k сообщений), «Видел Последний Деп» (активен до Combot-импорта).
#     Метрика: объединённый счётчик сообщений. ВАЖНО: сообщения дают ачивки,
#     но НЕ дают MMR.
#   • marriage: «Семейный человек» (состоит в браке прямо сейчас) — нужен флаг
#     активного брака в сборщике.
#
# При добавлении новых категорий не забыть CATEGORY_ORDER и зеркало на сайте
# (v0-voznya/lib/voznya-bot.ts: ACHIEVEMENTS + ACHIEVEMENT_CATEGORIES).
# ============================================================================


# Быстрый доступ по коду.
ACHIEVEMENTS_BY_CODE: dict[str, Achievement] = {a.code: a for a in ACHIEVEMENTS}

# Множество кодов «основных» достижений, которые нужно открыть для «Меллстрой
# Возни» (метрика all): всё, кроме событийных, секретных и самой метрики all.
CORE_ACHIEVEMENT_CODES: set[str] = {
    a.code
    for a in ACHIEVEMENTS
    if a.metric not in (METRIC_ALL, METRIC_EVENT) and not a.hidden
}
