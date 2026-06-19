"""Эволюционирующее отношение друна к игроку (аффинити) + журнал эпизодов.

``attitude.py`` даёт стойку из СТАТИСТИКИ (богач/бомж/боец) — она статична. Этот
модуль добавляет ЖИВОЕ отношение, которое копится из того, КАК человек ведёт
себя С ДРУНОМ: тепло/уважительно общается — аффинити растёт (друг, кореш);
хамит/наезжает на друна — падает (личная вражда). Это переживает один разговор:
кто бесил друна неделю, остаётся врагом, даже если сейчас написал нейтрально.

Храним в ``AiProfile.data["affinity"]`` = {"score": int(-100..100), "ts": iso,
"episodes": [{"ts","tone","gist"}, ...]} — последние эпизоды как короткая лента
«что между нами было», чтобы друн ссылался на конкретику («ты на прошлой неделе
меня тупым ботом обозвал»), а не только на абстрактный «осадок». Без новых
таблиц. LLM-классификация тона (haiku) с быстрым substring-фолбэком, чтобы
обновления никогда не блокировали и не валили ответ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger

logger = get_logger(__name__)

_MIN, _MAX = -100, 100
# Затухание к нейтралу: за сутки без контакта |score| уменьшается на столько.
_DECAY_PER_DAY = 4
# Сколько последних эпизодов держим в журнале (после — старые дропаются).
_EPISODE_KEEP = 12
# Эпизод считаем достойным журнала только если |sentiment| >= 1 (нейтрал — шум).
_EPISODE_MIN_TONE = 1

# Сигналы тона сообщения В АДРЕС друна (грубо, без LLM). Подстроки, нижний рег.
_WARM = (
    "спасибо", "спс", "благодар", "красав", "красава", "лучший", "люблю",
    "обожаю", "respect", "респект", "ты крут", "топ", "молодец", "няша",
    "добрый", "помог", "выручил", "обнял", "друг ты", "братан", "братишка",
)
_HOSTILE = (
    "тупой", "тупая", "тупой бот", "заткнись", "затухни", "пошёл нахуй",
    "пошел нахуй", "иди нахуй", "нахуй пошёл", "ненавиж", "бесишь", "бесит",
    "урод", "мудак", "долбоёб", "долбоеб", "дебил", "идиот", "кретин",
    "сдохни", "тварь", "конченый", "конченая", "обнаглел", "достал ты",
    "глупый бот", "тупой ии", "бот тупой",
)


@dataclass
class Affinity:
    """Накопленное личное отношение друна к игроку."""

    score: int  # -100 (личный враг) .. +100 (близкий)
    episodes: list[dict] = field(default_factory=list)  # последние «инциденты»

    @property
    def label(self) -> str:
        s = self.score
        if s <= -60:
            return "ЛИЧНЫЙ ВРАГ"
        if s <= -25:
            return "НЕДРУГ"
        if s < 25:
            return "НЕЙТРАЛ"
        if s < 60:
            return "ПРИЯТЕЛЬ"
        return "КОРЕШ"

    @property
    def directive(self) -> str:
        """Как накопленная история отношений красит общение (поверх стойки)."""
        s = self.score
        if s <= -60:
            return (
                "У вас с ним ЛИЧНАЯ ВОЙНА — он не раз тебя оскорблял. Держись "
                "холодно-враждебно, в любой удобный момент можешь приложить, "
                "тёплоты ноль. Но если он РЕЗКО сменил тон и реально мирится — "
                "заметь это, ты живой, а не злопамятный робот."
            )
        if s <= -25:
            return (
                "Он тебя подбешивал, осадок есть. Поддевай острее обычного, "
                "доверия мало, но без открытой войны."
            )
        if s < 25:
            return (
                "Ровные отношения, без истории. Веди по ситуации и по своей "
                "стойке к нему."
            )
        if s < 60:
            return (
                "Вы в неплохих отношениях — он к тебе по-нормальному. Можно "
                "теплее, по-приятельски, подколы беззлобные."
            )
        return (
            "Это твой КОРЕШ — общается с тобой тепло, не раз по-доброму. "
            "Держись как со своим в доску: по-братски, с заботой под слоем "
            "подъёбов, прикрой если что."
        )

    def render_episodes(self, *, limit: int = 4) -> str:
        """Краткая лента эпизодов «что между вами было» для контекста.

        Возвращает многострочный текст или пустую строку. Берём последние
        ``limit`` записей (свежие интереснее), показываем в хронологии.
        """
        eps = list(self.episodes or [])
        if not eps:
            return ""
        recent = eps[-limit:]
        lines: list[str] = []
        for ep in recent:
            try:
                tone = int(ep.get("tone", 0) or 0)
            except (TypeError, ValueError):
                tone = 0
            mark = "+" if tone > 0 else ("-" if tone < 0 else "·")
            gist = str(ep.get("gist") or "").strip()
            if not gist:
                continue
            # Дата без секунд: «06-18» — это всё, что важно для «давности».
            ts = str(ep.get("ts") or "")
            stamp = ts[5:10] if len(ts) >= 10 else ""
            lines.append(f"{mark} {stamp} {gist}"[:160])
        return "\n".join(lines)


def score_sentiment(text: str) -> int:
    """Грубая оценка тона реплики В АДРЕС друна: -2..+2. 0 — нейтрально.

    Substring-эвристика — быстрый детерминированный фолбэк, который работает
    даже когда LLM недоступен (нет ключа/таймаут). LLM-уточнение делает
    :func:`classify_tone_llm`.
    """
    low = (text or "").lower()
    warm = sum(1 for w in _WARM if w in low)
    hostile = sum(1 for h in _HOSTILE if h in low)
    raw = warm - hostile
    return max(-2, min(2, raw))


async def classify_tone_llm(
    session: AsyncSession, text: str, *, heuristic: int
) -> tuple[int, str]:
    """Уточняет тон реплики игрока к друну через дешёвую LLM.

    Возвращает ``(sentiment, gist)``: сентимент в шкале ``-2..+2`` и короткий
    «смысл» эпизода для журнала (1 фраза, без местоимений «он/ему» — суть).

    Дешёвый путь:
    * берём ``ROLE_EVENT_ANALYSIS`` (или fast_model, если не выставлен);
    * один вызов на эпизод, ответ строго формата ``tone|gist``;
    * любой сбой — деградация к эвристике без gist (журнал пропускается).
    """
    raw = (text or "").strip()
    if len(raw) < 4:
        return heuristic, ""

    try:
        from app.features.drun import config as drun_config
        from app.features.drun import provider as drun_provider

        cfg = await drun_config.get_config(session)
        if not cfg.usable:
            return heuristic, ""
        out = await drun_provider.chat(
            cfg,
            system=_TONE_SYSTEM,
            messages=[{"role": "user", "content": raw[:400]}],
            model=cfg.model_for(drun_config.ROLE_EVENT_ANALYSIS),
        )
        return _parse_tone(out, fallback=heuristic)
    except Exception as exc:  # noqa: BLE001
        logger.debug("classify_tone_llm failed: %s", exc)
        return heuristic, ""


_TONE_SYSTEM = (
    "Ты — классификатор отношения К ЧАТ-БОТУ ДРУНУ из одной реплики игрока. "
    "Отвечай СТРОГО одной строкой формата TONE|GIST. TONE — целое от -2 до 2 "
    "(-2 враждебно/оскорбительно к боту, -1 раздражённо, 0 нейтрально, "
    "1 тепло/уважительно, 2 очень тепло/мирится). GIST — короткая суть "
    "эпизода в 1 фразе на русском, до 80 символов, без подлежащего «он»/"
    "«игрок» (только действие/настроение, например: «обозвал тупым ботом» / "
    "«поблагодарил за слив инфы» / «извинился за прошлую ругань»). Никаких "
    "пояснений вне формата."
)


def _parse_tone(raw: str, *, fallback: int) -> tuple[int, str]:
    """Разбирает строку 'tone|gist'. Безопасно деградирует к fallback и ''."""
    text = (raw or "").strip().splitlines()
    if not text:
        return fallback, ""
    line = text[0].strip()
    if "|" not in line:
        return fallback, ""
    tone_str, _, gist = line.partition("|")
    try:
        tone = int(tone_str.strip())
    except (TypeError, ValueError):
        return fallback, ""
    tone = max(-2, min(2, tone))
    return tone, gist.strip()[:120]


def _decayed(score: int, days: float) -> int:
    """Затухание к нейтралу: |score| уменьшается ~_DECAY_PER_DAY в сутки."""
    if score == 0 or days <= 0:
        return score
    shrink = int(_DECAY_PER_DAY * days)
    if score > 0:
        return max(0, score - shrink)
    return min(0, score + shrink)


def apply_delta(prev_score: int, sentiment: int) -> int:
    """Новое значение аффинити после реплики данного тона.

    Враждебность бьёт сильнее симпатии (обиды копятся быстрее, чем доверие) —
    это делает друна правдоподобно злопамятным, но прощающим при усилии.
    """
    if sentiment == 0:
        return prev_score
    step = sentiment * (5 if sentiment > 0 else 7)
    return max(_MIN, min(_MAX, prev_score + step))


async def get_affinity(session: AsyncSession, user_id: int) -> Affinity:
    """Текущее аффинити игрока (с учётом затухания). Нейтрал, если профиля нет."""
    try:
        from app.models import AiProfile

        prof = await session.get(AiProfile, user_id)
        if prof is None:
            return Affinity(0)
        aff = (prof.data or {}).get("affinity") or {}
        score = int(aff.get("score", 0) or 0)
        ts = aff.get("ts")
        if ts:
            try:
                last = datetime.fromisoformat(ts)
                now = datetime.now(timezone.utc)
                days = max(0.0, (now - last).total_seconds() / 86400)
                score = _decayed(score, days)
            except (ValueError, TypeError):
                pass
        episodes = aff.get("episodes") or []
        if not isinstance(episodes, list):
            episodes = []
        return Affinity(max(_MIN, min(_MAX, score)), episodes=episodes)
    except Exception:  # noqa: BLE001
        logger.debug("get_affinity failed", exc_info=True)
        return Affinity(0)


async def record_interaction(
    session: AsyncSession, user_id: int, text: str
) -> tuple[int, str, int]:
    """Обновляет аффинити и журнал эпизодов по реплике игрока в адрес друна.

    Двухслойный пайплайн:

    1. Быстрая substring-эвристика (``score_sentiment``) — выполняется всегда,
       даже если LLM упал/выключен. Это база аффинити-скора.
    2. LLM-уточнение тона + ``gist`` (``classify_tone_llm``) — даёт точный тон
       (для сарказма/мата без явных слов) и короткую формулу эпизода. Зовём
       только если |эвристика| >= ``_EPISODE_MIN_TONE`` ИЛИ текст достаточно
       длинный, чтобы оправдать вызов модели (отсекаем тривиальные «ок»/«да»).

    Эпизод с непустым ``gist`` пишется в журнал (последние ``_EPISODE_KEEP``).
    Профиль создаётся лениво. Коммит — на вызывающем.

    Возвращает ``(sentiment, gist, prev_score)`` — чтобы вызывающий мог решить,
    не достоин ли тон отдельного ПАМЯТНОГО ЭПИЗОДА отношений (наезд/примирение
    в адрес друна — это поступок, а не только сдвиг аффинити). prev_score — это
    отношение ДО этой реплики (с затуханием), нужно для распознавания
    примирения (был врагом → вдруг тепло).
    """
    heuristic = score_sentiment(text)
    text_len = len((text or "").strip())
    try:
        from app.models import AiProfile

        prof = await session.get(AiProfile, user_id)
        prev = 0
        prev_episodes: list[dict] = []
        last_days = 0.0
        if prof is not None:
            aff = (prof.data or {}).get("affinity") or {}
            prev = int(aff.get("score", 0) or 0)
            eps_raw = aff.get("episodes") or []
            if isinstance(eps_raw, list):
                prev_episodes = list(eps_raw)
            ts = aff.get("ts")
            if ts:
                try:
                    last = datetime.fromisoformat(ts)
                    last_days = max(
                        0.0,
                        (datetime.now(timezone.utc) - last).total_seconds() / 86400,
                    )
                except (ValueError, TypeError):
                    pass

        prev_decayed = _decayed(prev, last_days)

        # LLM-уточнение зовём только когда есть шанс получить полезный сигнал:
        # либо эвристика что-то увидела, либо реплика достаточно объёмная.
        sentiment = heuristic
        gist = ""
        if abs(heuristic) >= _EPISODE_MIN_TONE or text_len >= 24:
            sentiment, gist = await classify_tone_llm(
                session, text, heuristic=heuristic,
            )

        # Нечего записывать и нет профиля — не плодим пустые строки.
        if prof is None and sentiment == 0 and not gist:
            return sentiment, gist, prev_decayed

        new_score = apply_delta(prev_decayed, sentiment)
        now_iso = datetime.now(timezone.utc).isoformat()

        episodes = list(prev_episodes)
        if gist and abs(sentiment) >= _EPISODE_MIN_TONE:
            episodes.append({"ts": now_iso, "tone": int(sentiment), "gist": gist})
            # Ротация: держим только последние N эпизодов.
            if len(episodes) > _EPISODE_KEEP:
                episodes = episodes[-_EPISODE_KEEP:]

        if prof is None:
            prof = AiProfile(user_id=user_id, data={})
            session.add(prof)
        data = dict(prof.data or {})
        data["affinity"] = {
            "score": new_score,
            "ts": now_iso,
            "episodes": episodes,
        }
        prof.data = data
        return sentiment, gist, prev_decayed
    except Exception:  # noqa: BLE001
        logger.debug("record_interaction failed", exc_info=True)
        return 0, "", 0
