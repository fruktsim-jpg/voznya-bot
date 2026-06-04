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
    Achievement("first_ezhka", "🌱", "Первая ешка", "Заработать первую ешку",
                "economy", "total_earned", 1, reward=10),
    Achievement("thousandaire", "💰", "Тысячник", "Заработать 1 000 ешек",
                "economy", "total_earned", 1000, reward=100),
    Achievement("magnate", "💰", "Магнат", "Заработать 10 000 ешек",
                "economy", "total_earned", 10000, reward=400),

    # --- 🎰 Казино ----------------------------------------------------------
    Achievement("ludoman", "🎰", "Лудоман", "Сыграть 10 раз в казино",
                "casino", "casino_games_count", 10, reward=50),
    Achievement("casino_grandpa", "🎰", "Казиношный дед", "Сыграть 100 раз в казино",
                "casino", "casino_games_count", 100, reward=150),

    # --- ⚔️ Дуэли -----------------------------------------------------------
    Achievement("duelist", "⚔️", "Дуэлянт", "Выиграть 1 дуэль",
                "duel", "duels_won", 1, reward=50),
    Achievement("gladiator", "⚔️", "Гладиатор", "Выиграть 25 дуэлей",
                "duel", "duels_won", 25, reward=200),

    # --- 📦 Клады -----------------------------------------------------------
    Achievement("treasure_hunter", "📦", "Кладоискатель", "Найти 1 клад",
                "treasure", "treasures_found", 1, reward=50),
    Achievement("treasure_master", "📦", "Охотник за кладом", "Найти 10 кладов",
                "treasure", "treasures_found", 10, reward=200),

    # --- 💍 Браки -----------------------------------------------------------
    Achievement("true_love", "💍", "Любовь существует", "Заключить первый брак",
                "marriage", "marriages_count", 1, reward=50),

    # --- 🏳️ Номинации -------------------------------------------------------
    Achievement("nominee", "🏳️", "Звезда дня", "Стать «Пидором дня» 1 раз",
                "nomination", "pidor_count", 1, reward=25),
    Achievement("nominee_regular", "🏳️", "Завсегдатай номинаций",
                "Стать «Пидором дня» 10 раз", "nomination", "pidor_count", 10, reward=150),

    # --- 👑 Легенды Возни ---------------------------------------------------
    Achievement("apteka_magnate", "💊", "Аптечный магнат", "500 успешных ферм",
                "legend", "farm_success_count", 500, reward=400),
    Achievement("already_red", "🔥", "Уже красный", "Серия фермы 30 дней",
                "legend", "max_farm_streak", 30, reward=300),
    Achievement("voznya_started", "⚔️", "Возня началась", "100 побед в дуэлях",
                "legend", "duels_won", 100, reward=500),
    Achievement("cursed_suitcase", "📦", "Ёбаный чемодан", "Найти 50 кладов",
                "legend", "treasures_found", 50, reward=500),
    Achievement("nomination_king", "🏳️", "Король номинаций", "50 раз «Пидор дня»",
                "legend", "pidor_count", 50, reward=500),
    Achievement("authority", "☢️", "Авторитет", "Заработать 25 000 ешек",
                "legend", "total_earned", 25000, reward=750),
    Achievement("catushka", "🎰", "Пошла катушка", "Сорвать джекпот в казино",
                "legend", METRIC_EVENT, reward=250),
    Achievement("last_dep", "🍺", "Последний деп", "Поставить всё в казино и проиграть",
                "legend", METRIC_EVENT, reward=50),
    Achievement("love_grave", "💍", "Любовь до гроба", "Прожить в браке 30 дней",
                "legend", METRIC_EVENT, reward=250),
    Achievement("mellstroy", "👑", "Меллстрой Возни", "Открыть все основные достижения",
                "legend", METRIC_ALL, reward=1500),

    # --- 🤫 Секретные (скрыты до открытия) ----------------------------------
    Achievement("ludik_secret", "🎰", "Лудик", "Проиграть крупную сумму в казино",
                SECRET_CATEGORY, "max_casino_loss", 500, reward=0, hidden=True),
    Achievement("no_luck", "💀", "Не фартануло", "Серия из 5 проигрышей в казино",
                SECRET_CATEGORY, "casino_loss_streak", 5, reward=0, hidden=True),
    Achievement("bag", "⚔️", "Мешок", "Серия из 5 поражений в дуэлях",
                SECRET_CATEGORY, "duel_loss_streak", 5, reward=0, hidden=True),
    Achievement("kladmen", "📦", "Кладмен", "Забрать клад почти мгновенно",
                SECRET_CATEGORY, METRIC_EVENT, reward=100, hidden=True),
    Achievement("ghost", "👻", "Призрак Возни", "Вернуться после долгого отсутствия",
                SECRET_CATEGORY, METRIC_EVENT, reward=50, hidden=True),
]

# Быстрый доступ по коду.
ACHIEVEMENTS_BY_CODE: dict[str, Achievement] = {a.code: a for a in ACHIEVEMENTS}

# Множество кодов «основных» достижений, которые нужно открыть для «Меллстрой
# Возни» (метрика all): всё, кроме событийных, секретных и самой метрики all.
CORE_ACHIEVEMENT_CODES: set[str] = {
    a.code
    for a in ACHIEVEMENTS
    if a.metric not in (METRIC_ALL, METRIC_EVENT) and not a.hidden
}
