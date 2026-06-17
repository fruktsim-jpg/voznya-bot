"""Динамическое настроение Тёмного друна (#7).

У друна есть СВОЁ настроение, которое меняется во времени от реальной обстановки
в мире Возни, а не только статичный характер. Настроение влияет на тон ответов:
радостный/возбуждённый/хаотичный/злой/подозрительный/разочарованный/довольный.

Считаем ДЕТЕРМИНИРОВАННО из дешёвых сигналов (без отдельного LLM-вызова), чтобы
можно было дёргать на каждый ответ:
* активность чата (сколько реплик за окно);
* свежие события мира (``world_events``) с учётом severity и типа;
* экономический пульс (крупные выигрыши/просадки за последнее время);
* состояние сезона (идёт ли, насколько активен).

Возвращаем :class:`Mood` (метка + интенсивность + краткая причина) и директиву
для инжекта в контекст. Любой сбой → нейтральное настроение (молча).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import memory as drun_memory
from app.models import WorldEvent

logger = get_logger(__name__)

# Метки настроений (как в задании).
MOOD_AMUSED = "amused"            # ему весело, рофлит
MOOD_DISAPPOINTED = "disappointed"  # вяло, разочарован тишиной
MOOD_SUSPICIOUS = "suspicious"    # что-то мутят, подозрителен
MOOD_EXCITED = "excited"          # на движе, заряжен
MOOD_ANGRY = "angry"              # злой, на взводе
MOOD_CHAOTIC = "chaotic"          # хаос, всё горит, кураж
MOOD_CELEBRATORY = "celebratory"  # праздник, кто-то поднял куш
MOOD_NEUTRAL = "neutral"          # ровный фон

# Окно «свежих» событий для оценки настроения.
_EVENT_WINDOW_MIN = 90
# Типы событий, которые тянут в «праздник» (крупный позитив).
_CELEBRATORY_TYPES = frozenset({
    "case_jackpot", "casino_jackpot", "treasure_found", "season_finalized",
    "achievement_legendary", "big_win",
})
# Типы-конфликты (тянут в хаос/злость).
_CONFLICT_TYPES = frozenset({
    "duel_won", "duel_lost", "rep_minus", "mute", "ban", "warn",
})


@dataclass
class Mood:
    """Текущее настроение друна."""

    label: str          # одна из MOOD_*
    intensity: int      # 1 (лёгкое) .. 3 (сильное)
    reason: str = ""    # короткое человекочитаемое «почему»

    def directive(self) -> str:
        """Инструкция для модели: как настроение красит тон ответа."""
        tone = _MOOD_TONE.get(self.label, _MOOD_TONE[MOOD_NEUTRAL])
        force = (
            "Сейчас это чувствуется СИЛЬНО." if self.intensity >= 3
            else "Оно фоном, не переигрывай." if self.intensity <= 1
            else "Оно заметно, но не карикатурно."
        )
        why = f" Повод: {self.reason}." if self.reason else ""
        return (
            f"# ТВОЁ НАСТРОЕНИЕ СЕЙЧАС: {self.label.upper()}.\n"
            f"{tone} {force}{why}\n"
            "Настроение красит ТОН реплики (резче/теплее/азартнее), но не "
            "заставляет менять смысл ответа и не пересказывается вслух."
        )


# Тон под каждое настроение (одна фраза-направление для модели).
_MOOD_TONE: dict[str, str] = {
    MOOD_AMUSED: "Тебе сейчас весело и легко: больше рофла, подколов, игривости.",
    MOOD_DISAPPOINTED: "Ты вялый и слегка разочарован: суше, с ноткой «ну и скукота».",
    MOOD_SUSPICIOUS: "Ты подозрителен: будто чуешь подвох, цепляешься, прищуриваешься.",
    MOOD_EXCITED: "Ты на движе и заряжен: энергично, быстро, с напором.",
    MOOD_ANGRY: "Ты на взводе и злой: жёстче обычного, резкие формулировки, мало терпения.",
    MOOD_CHAOTIC: "У тебя кураж и хаос: дерзко, непредсказуемо, на адреналине.",
    MOOD_CELEBRATORY: "У тебя праздничный кураж: громко, с фейерверком, поздравительно-токсично.",
    MOOD_NEUTRAL: "Ты в ровном расположении духа: обычный твой вайб.",
}


async def compute_mood(session: AsyncSession, *, channel: str = "chat") -> Mood:
    """Считает текущее настроение друна из реальной обстановки.

    Полностью детерминированно и дёшево (пара агрегатных запросов). Любой сбой
    блока деградирует к нейтральному фону, не валя ответ.
    """
    try:
        hot = await drun_memory.recent_chat_count(
            session, channel=channel, seconds=300
        )
    except Exception:  # noqa: BLE001
        logger.debug("mood: chat count failed", exc_info=True)
        hot = 0

    since = now_utc() - timedelta(minutes=_EVENT_WINDOW_MIN)
    celebratory = conflict = total_ev = 0
    big_amount = 0
    try:
        rows = (
            await session.execute(
                select(WorldEvent.type, WorldEvent.severity, WorldEvent.amount)
                .where(WorldEvent.created_at >= since)
                .order_by(WorldEvent.created_at.desc())
                .limit(120)
            )
        ).all()
        for etype, severity, amount in rows:
            total_ev += 1
            t = str(etype or "")
            if t in _CELEBRATORY_TYPES or (severity or 0) >= 3:
                celebratory += 1
            if t in _CONFLICT_TYPES:
                conflict += 1
            if amount and abs(int(amount)) > big_amount:
                big_amount = abs(int(amount))
    except Exception:  # noqa: BLE001
        logger.debug("mood: events scan failed", exc_info=True)

    return _classify(hot=hot, celebratory=celebratory, conflict=conflict,
                     total_ev=total_ev, big_amount=big_amount)


def _classify(
    *, hot: int, celebratory: int, conflict: int, total_ev: int, big_amount: int
) -> Mood:
    """Чистая функция: сигналы → настроение (тестируется без БД)."""
    # Праздник: есть крупный позитив за окно.
    if celebratory >= 2 or (celebratory >= 1 and big_amount >= 50_000):
        return Mood(
            MOOD_CELEBRATORY, intensity=3 if celebratory >= 3 else 2,
            reason="в мире только что подняли куш/джекпот",
        )
    # Хаос: всё кипит — и чат, и конфликты.
    if hot >= 25 and conflict >= 3:
        return Mood(MOOD_CHAOTIC, intensity=3, reason="чат кипит и все грызутся")
    # Злость: много конфликтов подряд.
    if conflict >= 5:
        return Mood(MOOD_ANGRY, intensity=2, reason="сплошные дуэли и наезды")
    # Возбуждён: чат активный.
    if hot >= 20:
        return Mood(MOOD_EXCITED, intensity=2, reason="движ идёт полным ходом")
    # Подозрителен: тишина в чате, но в мире что-то происходит.
    if hot <= 2 and total_ev >= 4:
        return Mood(
            MOOD_SUSPICIOUS, intensity=2,
            reason="в чате тихо, а в мире кто-то что-то мутит",
        )
    # Весело: умеренный движ с позитивом.
    if hot >= 8 and conflict <= 1:
        return Mood(MOOD_AMUSED, intensity=2, reason="лёгкий движ без драм")
    # Разочарован: мёртвая тишина.
    if hot == 0 and total_ev == 0:
        return Mood(MOOD_DISAPPOINTED, intensity=2, reason="чат спит, мир замер")
    return Mood(MOOD_NEUTRAL, intensity=1)