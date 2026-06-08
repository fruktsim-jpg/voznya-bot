"""Настройки сезонной системы Возни (Сезон 1).

==============================================================================
ЭТОТ ФАЙЛ МОЖНО РЕДАКТИРОВАТЬ, не зная программирования.
Меняй только ЧИСЛА справа от «=». Не трогай названия слева и скобки.
После изменения бота нужно перезапустить (или сбросить кэш dynamic-настроек).
==============================================================================

Сезон — это перезапуск прогрессии раз в N дней. Между сезонами сбрасываются:
сезонный MMR, сезонные титулы/ачивки, рейтинги. НЕ сбрасываются: аккаунты,
сообщения, постоянный прогресс. См. docs/SEASON_1_WIPE_AND_DESIGN.md.

Принципы баланса (чтобы не понадобился повторный вайп):
* нет бесконечной генерации ешек (все краны под кулдауном/лимитом/разовостью);
* daily + weekly mint ограничены, не превышают потолок ~60 ешек/день;
* антиабуз (перелив ешек, дуэль-фарм MMR, мультиаккаунт-ферма) включён.
"""

from __future__ import annotations

from dataclasses import dataclass


# --- Длительность сезона ----------------------------------------------------

SEASON_LENGTH_DAYS = 56  # 8 недель


# --- Дивизионы (по сезонному MMR) -------------------------------------------
#
# Дивизион определяется ТЕКУЩИМ сезонным MMR (копится с нуля каждый сезон).
# Пороги подобраны под ~10–185 MMR/день активной игры (см. аудит §7).


@dataclass(frozen=True)
class Division:
    """Сезонный дивизион по нижней границе season MMR (включительно)."""

    min_mmr: int
    emoji: str
    name: str
    # Награда ешками при ДОСТИЖЕНИИ дивизиона за сезон (разово на старте сезона
    # выдаётся 0; награда выдаётся в финале по итоговому дивизиону).
    reward_eshki: int


# Отсортировано по min_mmr (по возрастанию).
DIVISIONS: tuple[Division, ...] = (
    Division(0, "🥉", "Bronze", 0),
    Division(500, "🥈", "Silver", 200),
    Division(1500, "🥇", "Gold", 500),
    Division(3500, "💠", "Platinum", 1200),
    Division(7000, "💎", "Diamond", 2500),
    Division(12000, "🏅", "Master", 5000),
)


def get_division(season_mmr: int) -> Division:
    """Возвращает дивизион для указанного сезонного MMR."""
    current = DIVISIONS[0]
    for div in DIVISIONS:
        if season_mmr >= div.min_mmr:
            current = div
        else:
            break
    return current


# --- Daily reward (ежедневная награда за заход) -----------------------------
#
# Цикл из 7 дней: чем длиннее серия, тем больше награда. На 8-й день цикл
# повторяется с 1-го. Пропуск дня сбрасывает серию (login streak).

DAILY_REWARDS: tuple[int, ...] = (10, 12, 15, 18, 20, 25, 30)


def daily_reward_for_streak(streak_day: int) -> int:
    """Награда за день серии (1..N). Цикл по длине DAILY_REWARDS."""
    if streak_day < 1:
        return DAILY_REWARDS[0]
    idx = (streak_day - 1) % len(DAILY_REWARDS)
    return DAILY_REWARDS[idx]


# --- Login streak -----------------------------------------------------------

# Сколько часов «прощаем» при подсчёте серии (заход хотя бы раз в календарный
# день). Серия растёт на +1 при заходе в новый день, сбрасывается при пропуске.
STREAK_GRACE_HOURS = 0  # 0 = строго по календарным дням (UTC)


# --- Weekly missions (недельные задания) ------------------------------------
#
# Каждую неделю игроку доступен набор заданий. Выполнил — получил награду ешками
# (+ сезонный MMR). Набор фиксированный (ниже); прогресс копится за неделю и
# сбрасывается с началом новой недели.


@dataclass(frozen=True)
class Mission:
    """Недельное задание: счётчик до target по метрике metric."""

    code: str
    title: str
    metric: str  # farm / cases / duel_win / treasure / messages
    target: int
    reward_eshki: int
    reward_mmr: int


WEEKLY_MISSIONS: tuple[Mission, ...] = (
    Mission("w_farm", "Ферми 20 раз", "farm", 20, 60, 20),
    Mission("w_cases", "Открой 10 кейсов", "cases", 10, 50, 15),
    Mission("w_duel", "Выиграй 5 дуэлей", "duel_win", 5, 70, 25),
    Mission("w_treasure", "Забери 5 кладов", "treasure", 5, 50, 15),
)

# Метрики заданий (значение колонки прогресса). Держать в синхроне с WEEKLY_MISSIONS.
MISSION_METRIC_FARM = "farm"
MISSION_METRIC_CASES = "cases"
MISSION_METRIC_DUEL_WIN = "duel_win"
MISSION_METRIC_TREASURE = "treasure"
MISSION_METRIC_MESSAGES = "messages"


# --- Антиабуз ---------------------------------------------------------------

# Перелив ешек (подарок ешками игрок→игрок): дневные лимиты.
ESHKI_TRANSFER_MAX_PER_DAY = 500       # суммарно ешек в день одному отправителю
ESHKI_TRANSFER_MAX_COUNT_PER_DAY = 5   # число операций передачи в день

# Анти-дуэль-фарм MMR: участие даёт MMR ограниченное число раз в день и только
# с РАЗНЫМИ оппонентами (повтор с тем же оппонентом MMR за участие не даёт).
DUEL_MMR_PARTICIPATION_MAX_PER_DAY = 5

# Анти-мультиаккаунт фермы: ферма доступна только «прогретым» аккаунтам.
FARM_MIN_MESSAGES = 20        # минимум сообщений в чате до доступа к ферме
FARM_MIN_ACCOUNT_AGE_HOURS = 24  # минимальный возраст аккаунта (часы)


# --- Сезонный кейс ----------------------------------------------------------

SEASON_CASE_CODE = "case_season_1"
SEASON_CASE_PRICE = 600
SEASON_CASE_RTP_TARGET = 0.88  # цель RTP при сиде наград (сток сохраняется)

# Сезонные лимитки sell-rate: продать обратно нельзя (анти-кран). 0 = не продаётся.
SEASON_LIMITED_SELL_RATE = 0.0


# --- Сезонные титулы (выдаются в финале сезона) -----------------------------


@dataclass(frozen=True)
class SeasonTitle:
    """Сезонный титул, выдаётся в финале по условию."""

    code: str
    emoji: str
    name: str
    # Условие выдачи: "division:<name>" / "rank:1" / "rank:3" и т.п. —
    # интерпретируется сервисом финала сезона.
    condition: str


SEASON_TITLES: tuple[SeasonTitle, ...] = (
    SeasonTitle("s1_champion", "🏆", "Чемпион Сезона 1", "rank:1"),
    SeasonTitle("s1_top3", "🥇", "Призёр Сезона 1", "rank:3"),
    SeasonTitle("s1_master", "🏅", "Master S1", "division:Master"),
    SeasonTitle("s1_diamond", "💎", "Diamond S1", "division:Diamond"),
)


# --- Тексты -----------------------------------------------------------------

SEASON_CARD = (
    "🗓 <b>Сезон {season_name}</b>\n"
    "🏆 Сезонный MMR: <b>{season_mmr:,}</b>\n"
    "{div_emoji} Дивизион: <b>{div_name}</b>\n"
    "⏳ До конца сезона: <b>{days_left}</b> дн."
)

DAILY_CLAIMED = "🎁 Ежедневная награда: <b>+{amount}</b> ешек\n🔥 Серия: <b>{streak}</b> дн."
DAILY_ALREADY = "🎁 Сегодня награду уже забрал. Возвращайся завтра — серия растёт."
