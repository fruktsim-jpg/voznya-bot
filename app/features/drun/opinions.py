"""Долгосрочные МНЕНИЯ друна о каждом игроке — ядро личности (LEAP-4).

`affinity.py` даёт ОДНУ ось «тепло↔вражда ко мне» и затухает за дни. `attitude.py`
— статическая стойка из текущей статы. `worldview.opinion` — свободный текст из
думы. Ни то, ни другое не даёт МНОГОМЕРНОГО, МЕДЛЕННО эволюционирующего
отношения, из которого рождаются устойчивые любимчики, уважаемые, раздражающие и
заклятые — то, что делает участника узнаваемой личностью, а не нейтральным ИИ.

Этот модуль вводит на каждого активного игрока ВЕКТОР МНЕНИЯ по 7 осям (0..100):

* ``trust``         — доверие (держит слово, не кидает);
* ``respect``       — уважение (скилл, статус, винрейт, репутация чата);
* ``annoyance``     — раздражение (бесит, ноет, спамит, нарывается);
* ``interest``      — интерес (за ним любопытно следить, он непредсказуем);
* ``chaos``         — фактор хаоса (генерит движ/драму, тильтует, рискует);
* ``reliability``   — надёжность (стабилен, фермит, выполняет обещания);
* ``entertainment`` — развлекательная ценность (с ним смешно, он мемный).

КЛЮЧЕВОЕ свойство — ИНЕРЦИЯ: мнение меняется МЕДЛЕННО (EMA с малым α), копится
неделями и месяцами, и так же медленно стекает к нейтралу 50 без подтверждений.
Один эпизод почти не двигает вектор — нужна устойчивая картина. Это и отличает
«сложившееся мнение» от сиюминутной реакции (за неё отвечают emotion/affinity).

Вектор живёт в ``AiProfile.data["opinion"]`` (без миграции). Наблюдения —
детерминированные сигналы из статы/аффинити/поведения (дёшево, без LLM), копятся
фоновым свипом профилей. Чистая математика (evolve/decay/observe) тестируется
без БД.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger

logger = get_logger(__name__)

# Оси мнения. Порядок фиксирован (используется в рендере/тестах).
AXES = (
    "trust", "respect", "annoyance", "interest",
    "chaos", "reliability", "entertainment",
)
_NEUTRAL = 50.0
_MIN, _MAX = 0.0, 100.0

# Инерция: доля НОВОГО наблюдения в каждом обновлении (EMA α). Маленькая —
# мнение тяжёлое, один эпизод почти не сдвигает, нужно много подтверждений.
_ALPHA = 0.08
# Затухание к нейтралу: за сутки без наблюдений каждая ось приближается к 50 на
# эту долю остатка. Очень медленно — мнение держится неделями (полураспад ~24 дн).
_DECAY_PER_DAY = 0.028

# Человекочитаемые ярлыки осей (для рендера в досье).
_AXIS_RU: dict[str, str] = {
    "trust": "доверие",
    "respect": "уважение",
    "annoyance": "раздражает",
    "interest": "интерес",
    "chaos": "хаос-фактор",
    "reliability": "надёжность",
    "entertainment": "с ним весело",
}


@dataclass
class Opinion:
    """Многомерное сложившееся мнение друна об игроке (каждая ось 0..100)."""

    axes: dict[str, float]
    # Сколько наблюдений впитал вектор — мера «зрелости» мнения (нулевое = только
    # сложилось, ему меньше веры). Растёт с каждым observe.
    samples: int = 0

    def get(self, axis: str) -> float:
        return float(self.axes.get(axis, _NEUTRAL))

    @property
    def is_formed(self) -> bool:
        """Сложилось ли мнение (достаточно наблюдений, чтобы на него опираться)."""
        return self.samples >= 5

    def dominant(self) -> list[tuple[str, float]]:
        """Оси, заметно отклонённые от нейтрала, сильнейшие первыми.

        Возвращает [(axis, value)] для |value-50| >= 18 — это «выраженные» черты
        мнения, по которым друн реально ведёт себя по-разному с разными людьми.
        """
        out = [
            (ax, self.get(ax)) for ax in AXES
            if abs(self.get(ax) - _NEUTRAL) >= 18.0
        ]
        out.sort(key=lambda kv: abs(kv[1] - _NEUTRAL), reverse=True)
        return out

    def standing(self) -> str:
        """Одно-словный «социальный титул» игрока по сложившемуся мнению.

        Сжимает вектор в узнаваемую роль — любимчик/уважаемый/раздражающий/...
        Это то, что делает игрока УЗНАВАЕМЫМ для друна (и для читателя досье).
        """
        if not self.is_formed:
            return "ПРИСМАТРИВАЕТСЯ"  # мнение ещё не сложилось
        trust = self.get("trust")
        respect = self.get("respect")
        annoy = self.get("annoyance")
        ent = self.get("entertainment")
        chaos = self.get("chaos")
        rel = self.get("reliability")
        # Грубая, но читаемая классификация по доминантам.
        if annoy >= 70 and respect < 45:
            return "БЕСИТ"
        if trust >= 68 and respect >= 60:
            return "ЛЮБИМЧИК"
        if respect >= 70:
            return "УВАЖАЕМЫЙ"
        if ent >= 70:
            return "КЛОУН-ЛЮБИМЕЦ"  # с ним смешно, его держат для движа
        if chaos >= 72:
            return "БЕДОВЫЙ"
        if trust < 32:
            return "НЕ ВНУШАЕТ ДОВЕРИЯ"
        if rel >= 70 and ent < 45:
            return "СКУЧНЫЙ РАБОТЯГА"
        return "НА ЗАМЕТКЕ"

    def directive(self) -> str:
        """Инструкция для промпта: как сложившееся мнение красит поведение.

        Возвращает пустую строку для несложившегося/ровного мнения (не шумим).
        """
        dom = self.dominant()
        if not self.is_formed or not dom:
            return ""
        bits: list[str] = []
        for ax, val in dom[:3]:
            high = val >= _NEUTRAL
            phrase = _AXIS_PHRASE.get((ax, high))
            if phrase:
                bits.append(phrase)
        if not bits:
            return ""
        title = self.standing()
        return (
            f"# ТВОЁ СЛОЖИВШЕЕСЯ МНЕНИЕ О НЁМ [{title}] "
            f"(копилось долго, держись по нему, но не зачитывай вслух):\n- "
            + "\n- ".join(bits)
        )


# Фразы по (ось, высокая_ли). Высокая = значение выше нейтрала.
_AXIS_PHRASE: dict[tuple[str, bool], str] = {
    ("trust", True): "ты ему доверяешь — он не кидает, на него можно положиться",
    ("trust", False): "ты ему НЕ доверяешь — кидал/набрёхивал, держи ухо востро",
    ("respect", True): "ты его уважаешь за дело — скилл/статус заслужены",
    ("respect", False): "ты его не уважаешь — пустозвон без достижений",
    ("annoyance", True): "он тебя реально БЕСИТ — ноет/спамит/нарывается, тебя на него тянет огрызнуться",
    ("annoyance", False): "он тебя не раздражает, с ним спокойно",
    ("interest", True): "за ним тебе интересно следить — непредсказуемый, цепляет",
    ("interest", False): "он тебе скучноват, ничего нового",
    ("chaos", True): "он генератор хаоса и драмы — обожаешь это в нём подсвечивать",
    ("chaos", False): "он тихий и предсказуемый",
    ("reliability", True): "он надёжный и стабильный — фермит, держит слово",
    ("reliability", False): "он раздолбай — то пусто, то густо, слову грош цена",
    ("entertainment", True): "с ним ВЕСЕЛО — мемный, ты любишь его подъёбывать по-доброму",
    ("entertainment", False): "с ним пресно, юмора ноль",
}


def neutral() -> Opinion:
    """Свежий нейтральный вектор (все оси = 50)."""
    return Opinion(axes={ax: _NEUTRAL for ax in AXES}, samples=0)


def _clamp(x: float) -> float:
    return max(_MIN, min(_MAX, x))


def decay(axes: dict[str, float], days: float) -> dict[str, float]:
    """Медленное стекание всех осей к нейтралу 50 за ``days`` дней.

    Без подтверждений мнение постепенно тускнеет — но ОЧЕНЬ медленно (недели),
    так что давние любимчики/враги не обнуляются за пару тихих дней.
    """
    if days <= 0:
        return {ax: _clamp(axes.get(ax, _NEUTRAL)) for ax in AXES}
    # Доля, на которую остаток до нейтрала сожмётся за период (геометрически).
    keep = (1.0 - _DECAY_PER_DAY) ** days
    out: dict[str, float] = {}
    for ax in AXES:
        v = axes.get(ax, _NEUTRAL)
        out[ax] = _clamp(_NEUTRAL + (v - _NEUTRAL) * keep)
    return out


def evolve(
    axes: dict[str, float], observation: dict[str, float], *, alpha: float = _ALPHA
) -> dict[str, float]:
    """Сдвигает вектор к наблюдению через EMA (инерция: маленький α).

    ``observation`` — желаемые «целевые» значения осей из текущей картины (0..100).
    Каждая ось подтягивается к цели на долю ``alpha``. Оси, которых нет в
    наблюдении, не трогаем (нет сигнала — нет сдвига).
    """
    out = dict(axes)
    for ax in AXES:
        if ax not in observation:
            out[ax] = _clamp(axes.get(ax, _NEUTRAL))
            continue
        target = _clamp(float(observation[ax]))
        cur = axes.get(ax, _NEUTRAL)
        out[ax] = _clamp(cur + (target - cur) * alpha)
    return out


def observe_from_signals(
    *,
    affinity_score: int = 0,
    rep_score: int = 0,
    rep_minus: int = 0,
    duels_won: int = 0,
    duels_lost: int = 0,
    messages: int = 0,
    casino_loss_streak: int = 0,
    farm_streak: int = 0,
    duel_loss_streak: int = 0,
    balance: int = 0,
    pidor_count: int = 0,
) -> dict[str, float]:
    """Детерминированно строит «целевой» вектор-наблюдение из сигналов игрока.

    Это НЕ мнение, а мгновенный отпечаток текущей картины: куда мнение ДОЛЖНО
    медленно дрейфовать, если такая картина устойчива. Чистая функция.
    """
    total_duels = duels_won + duels_lost
    winrate = (duels_won / total_duels) if total_duels >= 4 else 0.5

    # respect: винрейт + репутация чата + статус (баланс/активность).
    respect = _NEUTRAL
    respect += (winrate - 0.5) * 80.0          # ±40 за крайние винрейты
    respect += max(-25.0, min(25.0, rep_score * 2.5))
    if balance >= 50_000:
        respect += 8.0
    if messages >= 500:
        respect += 6.0

    # trust: репутация + надёжность фермы − токсичные минусы репы.
    trust = _NEUTRAL
    trust += max(-20.0, min(20.0, rep_score * 2.0))
    trust -= min(25.0, rep_minus * 5.0)
    trust += min(12.0, farm_streak * 1.0)

    # annoyance: личное раздражение (низкое аффинити) + «пидор дня» как мем-маркер.
    annoyance = _NEUTRAL
    if affinity_score < 0:
        annoyance += min(40.0, -affinity_score * 0.5)
    else:
        annoyance -= min(20.0, affinity_score * 0.3)
    annoyance -= min(15.0, rep_score * 1.0) if rep_score > 0 else 0.0

    # interest: непредсказуемость — серии, тильт, активность.
    interest = _NEUTRAL
    interest += min(20.0, casino_loss_streak * 3.0)
    interest += min(15.0, duel_loss_streak * 3.0)
    if messages >= 300:
        interest += 8.0

    # chaos: тильт в казино + дуэльные сливы + «пидор дня».
    chaos = _NEUTRAL
    chaos += min(30.0, casino_loss_streak * 4.0)
    chaos += min(15.0, duel_loss_streak * 3.0)
    chaos += min(12.0, pidor_count * 3.0)

    # reliability: ферма-серия + положительная репа − казино-тильт.
    reliability = _NEUTRAL
    reliability += min(28.0, farm_streak * 2.2)
    reliability += max(-10.0, min(15.0, rep_score * 1.5))
    reliability -= min(20.0, casino_loss_streak * 3.0)

    # entertainment: личное тепло + хаос + «пидор дня» (мемность) − скука.
    entertainment = _NEUTRAL
    if affinity_score > 0:
        entertainment += min(22.0, affinity_score * 0.3)
    entertainment += min(15.0, casino_loss_streak * 2.0)  # тильт = контент
    entertainment += min(12.0, pidor_count * 3.0)
    if messages < 30:
        entertainment -= 12.0  # молчун — не развлекает

    return {
        "trust": _clamp(trust),
        "respect": _clamp(respect),
        "annoyance": _clamp(annoyance),
        "interest": _clamp(interest),
        "chaos": _clamp(chaos),
        "reliability": _clamp(reliability),
        "entertainment": _clamp(entertainment),
    }


def _parse(raw: dict | None) -> tuple[dict[str, float], int, str | None]:
    """Достаёт (axes, samples, ts) из сырого JSONB. Терпимо к мусору."""
    if not isinstance(raw, dict):
        return {ax: _NEUTRAL for ax in AXES}, 0, None
    axes_raw = raw.get("axes") if isinstance(raw.get("axes"), dict) else {}
    axes = {}
    for ax in AXES:
        try:
            axes[ax] = _clamp(float(axes_raw.get(ax, _NEUTRAL)))
        except (TypeError, ValueError):
            axes[ax] = _NEUTRAL
    try:
        samples = int(raw.get("samples", 0) or 0)
    except (TypeError, ValueError):
        samples = 0
    ts = raw.get("ts") if isinstance(raw.get("ts"), str) else None
    return axes, samples, ts


async def get_opinion(session: AsyncSession, user_id: int) -> Opinion:
    """Текущее мнение об игроке с учётом затухания. Нейтрал, если профиля нет."""
    try:
        from app.models import AiProfile

        prof = await session.get(AiProfile, user_id)
        if prof is None:
            return neutral()
        axes, samples, ts = _parse((prof.data or {}).get("opinion"))
        if ts:
            try:
                last = datetime.fromisoformat(ts)
                days = max(
                    0.0,
                    (datetime.now(timezone.utc) - last).total_seconds() / 86400.0,
                )
                axes = decay(axes, days)
            except (ValueError, TypeError):
                pass
        return Opinion(axes=axes, samples=samples)
    except Exception:  # noqa: BLE001
        logger.debug("get_opinion failed", exc_info=True)
        return neutral()


def merge_observation(
    raw_opinion: dict | None,
    observation: dict[str, float],
    *,
    now: datetime | None = None,
) -> dict:
    """Чистый шаг обновления хранимого мнения: decay(до сейчас) → evolve(к набл.).

    Возвращает новый JSONB-словарь ``{"axes","samples","ts"}`` для записи в
    профиль. Вынесено отдельно от БД, чтобы тестировать эволюцию без сессии.
    """
    now = now or datetime.now(timezone.utc)
    axes, samples, ts = _parse(raw_opinion)
    if ts:
        try:
            last = datetime.fromisoformat(ts)
            days = max(0.0, (now - last).total_seconds() / 86400.0)
            axes = decay(axes, days)
        except (ValueError, TypeError):
            pass
    axes = evolve(axes, observation)
    return {
        "axes": {ax: round(axes[ax], 2) for ax in AXES},
        "samples": samples + 1,
        "ts": now.isoformat(),
    }


def apply_deltas(
    raw_opinion: dict | None,
    deltas: dict[str, float],
    *,
    now: datetime | None = None,
) -> dict:
    """ПРЯМОЙ сдвиг осей памятным эпизодом (в обход медленного EMA).

    Агрегатное наблюдение (``merge_observation``) двигает мнение еле-еле — это
    правильно для статистики, но НЕ для памятного социального момента: предал,
    заступился, выполнил/слил обещание. Такой момент должен сдвинуть вектор
    СРАЗУ и ЗАМЕТНО, как у живого человека («после такого я о нём думаю иначе»).
    Поэтому здесь дельты применяются напрямую (с затуханием до «сейчас» и
    зажатием), а не подтягиваются к цели на долю α.

    ``deltas`` — карта ``ось → сдвиг`` (может быть отрицательной). samples тоже
    инкрементим: памятный эпизод — это «зрелое» свидетельство о человеке.
    """
    now = now or datetime.now(timezone.utc)
    axes, samples, ts = _parse(raw_opinion)
    if ts:
        try:
            last = datetime.fromisoformat(ts)
            days = max(0.0, (now - last).total_seconds() / 86400.0)
            axes = decay(axes, days)
        except (ValueError, TypeError):
            pass
    for ax, d in deltas.items():
        if ax in AXES:
            axes[ax] = _clamp(axes.get(ax, _NEUTRAL) + float(d))
    return {
        "axes": {ax: round(axes[ax], 2) for ax in AXES},
        "samples": samples + 1,
        "ts": now.isoformat(),
    }


async def nudge_opinion(
    session: AsyncSession, user_id: int, deltas: dict[str, float]
) -> bool:
    """Применяет прямой сдвиг мнения к профилю игрока. Коммит — на вызывающем.

    Лениво создаёт профиль, если его нет (памятный эпизод с новичком — повод
    завести досье). Возвращает True при успехе. Любой сбой — молча False.
    """
    if not deltas:
        return False
    try:
        from app.models import AiProfile

        prof = await session.get(AiProfile, user_id)
        if prof is None:
            prof = AiProfile(user_id=user_id, data={})
            session.add(prof)
        data = dict(prof.data or {})
        data["opinion"] = apply_deltas(data.get("opinion"), deltas)
        prof.data = data
        return True
    except Exception:  # noqa: BLE001
        logger.debug("nudge_opinion failed", exc_info=True)
        return False


# --- Любимчики и враги: рейтинг по чату --------------------------------------

def favorite_score(op: Opinion) -> float:
    """Сводный «любимчик-балл»: тепло/уважение/веселье минус раздражение.

    Положительный — друн к нему тяготеет (любимчик/уважаемый), отрицательный —
    избегает/недолюбливает. Используется для ранжирования по чату.
    """
    return (
        (op.get("trust") - _NEUTRAL)
        + (op.get("respect") - _NEUTRAL)
        + (op.get("entertainment") - _NEUTRAL)
        - (op.get("annoyance") - _NEUTRAL)
    )


@dataclass
class SocialStanding:
    """Место игрока в социальной картине друна (для контекста/инициатив)."""

    user_id: int
    name: str
    standing: str
    favorite_score: float


async def rank_chat(
    session: AsyncSession, *, limit: int = 200
) -> tuple[list[SocialStanding], list[SocialStanding]]:
    """Возвращает (любимчики, враги/раздражающие) среди игроков со сложившимся мнением.

    Сканирует профили с непустым ``opinion``, считает favorite_score (с учётом
    затухания), отбирает только СЛОЖИВШИЕСЯ мнения и сортирует по краям. Дёшево
    (один проход), вызывается автономным тиком и для «социального чутья».
    """
    from app.features.drun.names import name_for, resolve_names
    from app.models import AiProfile

    try:
        rows = (
            await session.execute(
                select(AiProfile.user_id, AiProfile.data)
                .where(AiProfile.data.has_key("opinion"))  # type: ignore[attr-defined]
                .limit(limit)
            )
        ).all()
    except Exception:  # noqa: BLE001
        logger.debug("rank_chat query failed", exc_info=True)
        return [], []

    now = datetime.now(timezone.utc)
    scored: list[tuple[int, Opinion, float]] = []
    for uid, data in rows:
        axes, samples, ts = _parse((data or {}).get("opinion"))
        if ts:
            try:
                days = max(
                    0.0,
                    (now - datetime.fromisoformat(ts)).total_seconds() / 86400.0,
                )
                axes = decay(axes, days)
            except (ValueError, TypeError):
                pass
        op = Opinion(axes=axes, samples=samples)
        if not op.is_formed:
            continue
        scored.append((uid, op, favorite_score(op)))

    if not scored:
        return [], []
    names = await resolve_names(session, [uid for uid, _, _ in scored])
    favs = sorted(scored, key=lambda t: t[2], reverse=True)
    foes = sorted(scored, key=lambda t: t[2])

    def _mk(items: list[tuple[int, Opinion, float]], positive: bool) -> list[SocialStanding]:
        out: list[SocialStanding] = []
        for uid, op, sc in items:
            if positive and sc <= 15:
                break
            if not positive and sc >= -15:
                break
            out.append(
                SocialStanding(uid, name_for(names, uid), op.standing(), round(sc, 1))
            )
        return out[:5]

    return _mk(favs, True), _mk(foes, False)
