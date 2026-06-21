"""Сборка контекста для модели: что друн «видит» перед ответом.

Перед каждым запросом автоматически подмешиваем:
* статистику игрока (баланс, MMR, репутация, дуэли, сообщения) — если запрос
  про конкретного игрока;
* информацию о сезоне (активен ли, топ);
* последние события мира (``world_events``);
* релевантные факты из долгосрочной памяти.

Всё — только чтение. Возвращаем компактный текстовый блок (он уйдёт в user-роль
вместе с конкретным заданием). Любой сбой отдельного блока не валит весь
контекст — деградируем по частям.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.money import money
from app.core.utils import now_utc
from app.features.drun import attitude as drun_attitude
from app.features.drun import chat_archive as drun_chat_archive
from app.features.drun import memory as drun_memory
from app.features.drun import memory_recall as drun_memory_recall
from app.features.drun import identity as drun_identity
from app.features.drun.names import name_for, resolve_names, resolve_person_hints
from app.models import User, WorldEvent

logger = get_logger(__name__)

_T = TypeVar("_T")


class ContextIntent(StrEnum):
    """Намерение текущего запроса — определяет, какие блоки тащить в prompt."""

    DEFAULT = "default"
    PAST = "past"
    PERSON = "person"
    ECONOMY = "economy"
    WEB = "web"
    OWNER = "owner"


@dataclass(frozen=True)
class ContextRoute:
    """Какие блоки контекста включать под конкретное намерение.

    Цель не идеальный NLU, а чтобы НЕ каждый ответ получал все тяжёлые блоки
    сразу (память + архив + экономика + мир + web). Экономика остаётся фоном по
    умолчанию для Model-2-осведомлённости, но past/person приоритезируют
    социальную память и историю.
    """

    intent: ContextIntent
    include_recent_chat: bool = True
    include_memory: bool = True
    include_archive: bool = False
    include_web: bool = False
    include_overview: bool = True
    include_worldview: bool = True
    include_economy: bool = True
    include_identity: bool = False
    archive_limit: int = 4


_PAST_WORDS = (
    "помнишь", "вспомни", "когда", "раньше", "раньш", "стар", "архив",
    "истори", "было", "писал", "писала", "писали", "говорил", "говорила",
    "цитат", "сообщен", "что было",
)
_ECONOMY_WORDS = (
    "ешк", "баланс", "деньг", "казино", "ставк", "банк", "богат", "бедн",
    "зарплат", "эконом", "топ по деньгам", "кошел", "монет",
)
_WEB_WORDS = (
    "найди", "погугли", "загугли", "в интернете", "ссылк", "новост",
    "погода", "курс", "что такое", "когда выш", "актуальн",
    "сейчас в мире", "web", "search", "google",
)
_PERSON_WORDS = (
    "кто такой", "кто такая", "что знаешь про", "расскажи про", "досье",
    "расскажи о", "расскажи об", "что знаешь о", "что знаешь об",
    "профиль", "характер", "память про", "память о", "про него", "про неё", "про нее",
)


def _route_has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def classify_context_route(
    query: str | None,
    *,
    channel: str = "chat",
    subject_id: int | None = None,
) -> ContextRoute:
    """Детерминированная маршрутизация контекста по намерению запроса."""
    q = (query or "").lower().strip()
    if channel == "owner_dm":
        return ContextRoute(
            intent=ContextIntent.OWNER,
            include_recent_chat=False,
            include_archive=False,
            include_web=False,
            include_overview=True,
            include_worldview=True,
            include_economy=True,
            archive_limit=0,
        )
    # Person lookup must win over generic web wording. Russian queries like
    # "кто такая Карина" look like fact/web questions syntactically, but in this
    # product they usually mean "resolve a chat person and show dossier".
    if subject_id is not None or _route_has_any(q, _PERSON_WORDS):
        return ContextRoute(
            intent=ContextIntent.PERSON,
            include_archive=_route_has_any(q, _PAST_WORDS),
            include_web=False,
            include_overview=False,
            include_worldview=True,
            include_economy=False,
            include_identity=True,
            archive_limit=4,
        )
    if _route_has_any(q, _WEB_WORDS):
        return ContextRoute(
            intent=ContextIntent.WEB,
            include_archive=_route_has_any(q, _PAST_WORDS),
            include_web=True,
            include_overview=False,
            include_worldview=False,
            include_economy=False,
            archive_limit=3,
        )
    if _route_has_any(q, _PAST_WORDS):
        return ContextRoute(
            intent=ContextIntent.PAST,
            include_archive=True,
            include_web=False,
            include_overview=False,
            include_worldview=True,
            include_economy=False,
            include_identity=True,
            archive_limit=8,
        )
    if _route_has_any(q, _ECONOMY_WORDS):
        return ContextRoute(
            intent=ContextIntent.ECONOMY,
            include_archive=False,
            include_web=False,
            include_overview=True,
            include_worldview=False,
            include_economy=True,
            archive_limit=0,
        )
    return ContextRoute(
        intent=ContextIntent.DEFAULT,
        include_archive=False,
        include_web=False,
        include_overview=True,
        include_worldview=True,
        include_economy=True,
        archive_limit=0,
    )


class ContextBuildResult(str):
    """String context with ids of memory/archive rows shown to the model."""

    memory_ids: list[int]
    archive_ids: list[int]

    def __new__(
        cls,
        value: str,
        *,
        memory_ids: list[int] | None = None,
        archive_ids: list[int] | None = None,
    ):
        obj = str.__new__(cls, value)
        obj.memory_ids = memory_ids or []
        obj.archive_ids = archive_ids or []
        return obj


async def _isolated(
    session: AsyncSession,
    name: str,
    coro_factory: Callable[[], Awaitable[_T]],
    default: _T,
) -> _T:
    """Запускает блок контекста в собственном SAVEPOINT.

    INCIDENT 2026-06-18 part 4: каждый из 28+ ``_*_block`` в этом файле
    ловит ``except Exception`` и тихо возвращает пустую строку. Но если
    внутри упал SQL (отсутствие fact_tsv/pg_trgm/vector, schema drift,
    embedder вернул ValueError из middleware-кода, etc.), asyncpg
    переводит ОБЩУЮ транзакцию хэндлера в ``aborted`` — и СЛЕДУЮЩИЙ
    ``_*_block`` падает на самом первом SELECT с
    ``InFailedSQLTransactionError``, ловится тем же ``except Exception``,
    логируется как «такой-то блок failed», но к этому моменту вся
    последующая работа generate() (recent_messages, add_message,
    affinity-write) уже невозможна — друн молчит.

    Раньше в service.py:208 это пытались закрыть ОДНИМ savepoint вокруг
    всей фазы сборки промпта. Не работает: savepoint роллбэчится только
    при выходе из ``async with``, а внутри best-effort блоки уже
    проглотили исключение — выйти из savepoint с rollback'ом некому,
    а RELEASE SAVEPOINT в конце уже падает на отравленной транзакции.

    Этот helper — правильный уровень изоляции: каждый блок открывает СВОЙ
    savepoint, при любом исключении делается ROLLBACK TO SAVEPOINT, и
    ВНЕШНЯЯ транзакция остаётся чистой. ``default`` — что вернуть при
    сбое (обычно "" для блоков-строк, [] для списков). ``name`` —
    только для лога.
    """
    try:
        async with session.begin_nested():
            return await coro_factory()
    except Exception:  # noqa: BLE001
        logger.warning("drun ctx block failed: %s", name, exc_info=True)
        return default


async def _player_assets_block(session: AsyncSession, user_id: int) -> list[str]:
    """Дополнительная видимость по игроку: инвентарь, ачивки, сезон, модерация,
    активность в кейсах/подарках. Каждый под-блок изолирован try/except — любой
    сбой не рушит досье. Возвращает список строк (может быть пустым).

    Под-блоки оформлены отдельными корутинами для читаемости, но выполняются
    последовательно: общая AsyncSession не допускает конкурентных запросов.
    """
    async def _inv() -> str:
        try:
            from app.repositories import inventory as inv_repo

            total = await inv_repo.count_items(session, user_id)
            distinct = await inv_repo.count_distinct_items(session, user_id)
            if total:
                return f"- Инвентарь: {total} предметов ({distinct} видов)"
        except Exception:  # noqa: BLE001
            logger.debug("inv block failed", exc_info=True)
        return ""

    async def _ach() -> str:
        try:
            from app.features.achievements import service as ach_service

            codes = await ach_service.get_unlocked_codes(session, user_id)
            if codes:
                return f"- Ачивок открыто: {len(codes)}"
        except Exception:  # noqa: BLE001
            logger.debug("ach block failed", exc_info=True)
        return ""

    async def _season() -> str:
        try:
            from app.repositories import season as season_repo

            smmr = await season_repo.get_season_mmr(session, user_id)
            streak = await season_repo.get_streak(session, user_id)
            bits = []
            if smmr:
                bits.append(f"сезонный MMR {smmr}")
            if streak is not None and getattr(streak, "current_streak", 0):
                bits.append(f"заход подряд {streak.current_streak} дн")
            if bits:
                return "- Сезон: " + ", ".join(bits)
        except Exception:  # noqa: BLE001
            logger.debug("season block failed", exc_info=True)
        return ""

    async def _mod() -> str:
        try:
            from app.repositories import moderation as mod_repo

            state = await mod_repo.get_state(session, user_id)
            if state is not None:
                warns = int(getattr(state, "warn_count", 0) or 0)
                mbits = []
                if getattr(state, "banned_until", None):
                    mbits.append("ЗАБАНЕН")
                if getattr(state, "muted_until", None):
                    mbits.append("в муте")
                if warns:
                    mbits.append(f"варнов {warns}")
                if mbits:
                    return "- Модерация: " + ", ".join(mbits)
        except Exception:  # noqa: BLE001
            logger.debug("mod block failed", exc_info=True)
        return ""

    async def _cases() -> str:
        try:
            from app.repositories import cases as cases_repo

            opened = await cases_repo.count_openings(session, user_id)
            if opened:
                return f"- Открыл кейсов: {opened}"
        except Exception:  # noqa: BLE001
            logger.debug("cases block failed", exc_info=True)
        return ""

    async def _gifts() -> str:
        try:
            from sqlalchemy import func as _f

            from app.models import GiftTransaction

            sent = await session.scalar(
                select(_f.count()).select_from(GiftTransaction)
                .where(GiftTransaction.sender_user_id == user_id)
                .where(GiftTransaction.gift_type == "player")
            )
            recv = await session.scalar(
                select(_f.count()).select_from(GiftTransaction)
                .where(GiftTransaction.recipient_user_id == user_id)
                .where(GiftTransaction.gift_type == "player")
            )
            bits = []
            if sent:
                bits.append(f"подарил {int(sent)}")
            if recv:
                bits.append(f"получил {int(recv)}")
            if bits:
                return "- Подарки: " + ", ".join(bits)
        except Exception:  # noqa: BLE001
            logger.debug("gifts block failed", exc_info=True)
        return ""

    # ВАЖНО: одну AsyncSession НЕЛЬЗЯ дёргать конкурентно (SQLAlchemy бросит
    # «another operation is in progress»), поэтому под-блоки идут последовательно.
    # Запросы дешёвые и по индексам, а путь ответа ограничен кулдауном — суммарная
    # задержка незаметна. Параллелизм тут дал бы баг, а не выигрыш.
    out: list[str] = []
    for sub in (_inv, _ach, _season, _mod, _cases, _gifts):
        line = await sub()
        if line:
            out.append(line)
    return out


def _behavior_hooks(user) -> list[str]:
    """Поведенческие «зацепки» для подколов из уже загруженного User.

    Без новых запросов: тильт в казино, серии проигрышей, активность фермы —
    самый сочный материал для роастов, который раньше друн вообще не видел.
    """
    from app.core.utils import now_utc

    out: list[str] = []
    loss_streak = int(getattr(user, "casino_loss_streak", 0) or 0)
    max_loss = int(getattr(user, "max_casino_loss", 0) or 0)
    if loss_streak >= 3:
        out.append(f"- В КАЗИНО ТИЛЬТ: {loss_streak} проигрышей подряд (есть чем подколоть)")
    if max_loss >= 5000:
        out.append(f"- Рекордный слив в казино: {max_loss} (за раз)")
    duel_ls = int(getattr(user, "duel_loss_streak", 0) or 0)
    if duel_ls >= 3:
        out.append(f"- В дуэлях сыпется: {duel_ls} поражений подряд")
    fstreak = int(getattr(user, "farm_streak", 0) or 0)
    if fstreak >= 5:
        out.append(f"- Фермит исправно: серия {fstreak} дней подряд")
    last_farm = getattr(user, "last_farm_at", None)
    if last_farm is not None:
        try:
            days = (now_utc() - last_farm).days
            if days >= 3:
                out.append(f"- НЕ ФЕРМИЛ уже {days} дн. (забил на ферму)")
        except Exception:  # noqa: BLE001
            pass
    pidor = int(getattr(user, "pidor_count", 0) or 0)
    if pidor >= 3:
        out.append(f"- Был «пидором дня» {pidor} раз")
    return out


async def _player_block(session: AsyncSession, user_id: int) -> str:
    """Досье игрока: статистика + брак + ОТНОШЕНИЕ Друна к нему.

    Это самый важный блок: он делает ответ персональным. Кроме сухих цифр сюда
    идёт «стойка» (stance) — как Друну держаться именно с этим человеком, плюс
    расширенная видимость: инвентарь, ачивки, сезон, модерация, кейсы.
    """
    try:
        from app.repositories import reputation as rep_repo

        user = await session.get(User, user_id)
        if user is None:
            return ""
        rep = await rep_repo.get_summary(session, user_id)
        rep_score = getattr(rep, "score", 0) or 0
        rep_plus = getattr(rep, "plus", 0) or 0
        rep_minus = getattr(rep, "minus", 0) or 0
        name = user.display_name()

        lines = [
            f"# ДОСЬЕ НА СОБЕСЕДНИКА: {name} (id={user_id})",
            "# (Это твоя ПАМЯТЬ о нём. НЕ зачитывай эти цифры в ответе — "
            "доставай деталь, только если она реально в тему разговора.)",
            f"- Баланс: {money(user.balance)}, всего заработано: "
            f"{money(getattr(user, 'total_earned', 0))}",
            f"- MMR: {getattr(user, 'mmr', 0)}, дуэли: "
            f"{getattr(user, 'duels_won', 0)}W/{getattr(user, 'duels_lost', 0)}L",
            f"- Репутация в чате: {rep_score:+d} (плюсов {rep_plus}, минусов {rep_minus})",
            f"- Сообщений в чате: {getattr(user, 'messages_count', 0)}",
        ]

        # Брак — повод для подколов/контекста отношений. Единый источник пары
        # — relationships.spouse_of (та же конвенция, что в профиле и графе).
        try:
            from app.features.drun import relationships as rel_mod

            partner_id = await rel_mod.spouse_of(session, user_id)
            if partner_id is not None:
                pnames = await resolve_names(session, [partner_id])
                lines.append(f"- В браке с {name_for(pnames, partner_id)}")
        except Exception:  # noqa: BLE001
            logger.debug("marriage lookup failed", exc_info=True)

        # Стойка Друна к этому игроку — ключ к персональности.
        stance = await drun_attitude.get_stance(session, user_id)
        if stance is not None:
            lines.append(
                f"- ТВОЁ ОТНОШЕНИЕ [{stance.label}]: {stance.directive}"
            )

        # Накопленное ЛИЧНОЕ отношение (как он вёл себя С ТОБОЙ во времени) —
        # поверх статической стойки. Делает дружбу/вражду историчными.
        try:
            from app.features.drun import affinity as drun_affinity

            aff = await drun_affinity.get_affinity(session, user_id)
            if aff.label != "НЕЙТРАЛ":
                lines.append(
                    f"- ВАША ИСТОРИЯ [{aff.label}]: {aff.directive}"
                )
            # Журнал эпизодов: короткие конкретные «что между вами было»,
            # подключаем даже на НЕЙТРАЛЕ — позволяет друну ссылаться на
            # реальные реплики («ты вчера обозвал меня тупым ботом»), вместо
            # абстрактной «истории отношений». Без него LLM придумывает.
            episodes_block = aff.render_episodes(limit=4)
            if episodes_block:
                lines.append("- ПОСЛЕДНИЕ ЭПИЗОДЫ С НИМ:")
                for ep_line in episodes_block.split("\n"):
                    lines.append(f"  {ep_line}")
        except Exception:  # noqa: BLE001
            logger.debug("affinity block failed", exc_info=True)

        # Что друн САМ надумал об этом игроке (его сформированное мнение из думы
        # о мире). Делает реакции укоренёнными в его сложившейся картине.
        try:
            from app.models import AiMemory

            opinions = (
                await session.execute(
                    select(AiMemory.fact)
                    .where(AiMemory.kind == "opinion")
                    .where(AiMemory.subject_id == user_id)
                    .order_by(AiMemory.weight.desc(), AiMemory.updated_at.desc())
                    .limit(2)
                )
            ).all()
            for (op,) in opinions:
                if op:
                    lines.append(f"- ТВОЁ МНЕНИЕ О НЁМ: {op}")
        except Exception:  # noqa: BLE001
            logger.debug("opinion block failed", exc_info=True)

        # СЛОЖИВШЕЕСЯ МНОГОМЕРНОЕ МНЕНИЕ (LEAP-4): вектор, копившийся неделями
        # (доверие/уважение/раздражение/интерес/хаос/надёжность/веселье). Даёт
        # узнаваемую социальную роль (любимчик/уважаемый/бесит/...) и красит
        # поведение поверх сиюминутной эмоции — суть устойчивой личности.
        try:
            from app.features.drun import opinions as drun_opinions

            op_vec = await drun_opinions.get_opinion(session, user_id)
            op_dir = op_vec.directive()
            if op_dir:
                lines.append(op_dir)
        except Exception:  # noqa: BLE001
            logger.debug("opinion vector block failed", exc_info=True)

        # ЧТО ОН ДЕЛАЛ (LEAP-5): памятные социальные ЭПИЗОДЫ — конкретные
        # поступки (предал/заступился/слил обещание/унизил/помирился), а не
        # агрегаты. Это «история отношений важнее сырой статы»: друн ссылается
        # на конкретный момент, а не на средние цифры. Значимые живут месяцами.
        try:
            from app.features.drun import episodes as drun_episodes

            eps = await drun_episodes.recent_episodes(session, user_id, limit=5)
            ep_block = drun_episodes.render_block(eps)
            if ep_block:
                lines.append(ep_block)
        except Exception:  # noqa: BLE001
            logger.debug("episodes block failed", exc_info=True)

        # Расширенная видимость: инвентарь/ачивки/сезон/модерация/кейсы.
        try:
            lines.extend(await _player_assets_block(session, user_id))
        except Exception:  # noqa: BLE001
            logger.debug("assets block failed", exc_info=True)

        # Поведенческие зацепки (тильт/серии/ферма) из уже загруженного user —
        # без новых запросов.
        try:
            lines.extend(_behavior_hooks(user))
        except Exception:  # noqa: BLE001
            logger.debug("behavior hooks failed", exc_info=True)

        # Денежные движения игрока за неделю (нафармил/просадил/поднял) — друн
        # видит, ЧЕМ человек живёт, и бьёт точечно вместо общих фраз.
        try:
            from app.features.drun import economy as drun_economy

            money_line = await drun_economy.player_money_digest(
                session, user_id, days=7
            )
            if money_line:
                lines.append(money_line)
            # Соц-граф: с кем игрок чаще всего пересекается (дуэли/подарки).
            rel_raw = await drun_economy.player_relations_digest(
                session, user_id, days=14
            )
            if rel_raw.startswith("RELATIONS:"):
                pairs = []
                for chunk in rel_raw[len("RELATIONS:"):].split(","):
                    uid_s, _, n_s = chunk.partition(":")
                    try:
                        pairs.append((int(uid_s), int(n_s)))
                    except ValueError:
                        continue
                if pairs:
                    rnames = await resolve_names(session, [u for u, _ in pairs])
                    rel_bits = [
                        f"{name_for(rnames, u)} ({n})" for u, n in pairs
                    ]
                    lines.append(
                        "- Чаще всего пересекается (дуэли/подарки): "
                        + ", ".join(rel_bits)
                    )
        except Exception:  # noqa: BLE001
            logger.debug("player money digest failed", exc_info=True)

        # Собранный портрет (личность + манера речи + темы): делает друна
        # «знающим» собеседника как человека, а не по сухим цифрам.
        try:
            from app.models import AiProfile

            prof = await session.get(AiProfile, user_id)
            if prof is not None:
                pdata = prof.data or {}
                # Идентичность со слов самого человека — приоритетна.
                pref = (pdata.get("preferred_name") or "").strip()
                if pref:
                    lines.append(f"- ПРОСИЛ ЗВАТЬ ЕГО: {pref} (используй это имя)")
                aliases = pdata.get("aliases") or []
                if aliases:
                    alias_str = ", ".join(
                        str(a.get("alias", "")).strip()
                        for a in aliases[:6] if a.get("alias")
                    )
                    if alias_str:
                        lines.append(f"- В ЧАТЕ ЕГО ТАКЖЕ ЗОВУТ: {alias_str}")
                gender = (pdata.get("gender") or "unknown").strip()
                if gender == "male":
                    lines.append("- ПОЛ: мужской (говори о нём в мужском роде)")
                elif gender == "female":
                    lines.append("- ПОЛ: женский (говори о ней в женском роде, "
                                 "это девушка — не лажай с родом)")
                else:
                    lines.append("- ПОЛ НЕИЗВЕСТЕН: НЕ угадывай род. Стройся "
                                 "нейтрально (обращайся по нику/на «ты», "
                                 "избегай родовых окончаний типа «сделал/"
                                 "сделала») — лучше нейтрально, чем не угадать.")
                if prof.summary:
                    lines.append(f"- ЛИЧНОСТЬ: {prof.summary[:400]}")
                if prof.speech_style:
                    lines.append(f"- МАНЕРА РЕЧИ: {prof.speech_style[:300]}")
                self_facts = pdata.get("self_facts") or []
                if self_facts:
                    lines.append("- ФОНОВО ЗНАЕШЬ О НЁМ (НЕ зачитывай, НЕ "
                                 "припоминай без повода — только если САМ "
                                 "поднял тему): " + "; ".join(self_facts[:8]))
                traits = pdata.get("traits") or []
                if traits:
                    lines.append("- ЧЕРТЫ (фон, не перечисляй): "
                                 + "; ".join(traits[:5]))
                topics = pdata.get("topics") or []
                if topics:
                    lines.append("- ОБЫЧНО ГОВОРИТ ПРО (НЕ навязывай эти темы "
                                 "сам — следуй за тем, что он пишет СЕЙЧАС): "
                                 + ", ".join(topics[:5]))
                rels = pdata.get("relationships") or []
                if rels:
                    label = {
                        "rival": "соперник —",
                        "ally": "симпатизирует", "foe": "недолюбливает",
                        "buddy": "кореша с",
                        "gifter": "дарит подарки —",
                    }
                    # Брак уже отрендерен отдельной строкой выше — не дублируем.
                    rel_str = "; ".join(
                        f"{label.get(r.get('kind'), r.get('kind'))} {r.get('name')}"
                        for r in rels[:6]
                        if r.get("name") and r.get("kind") != "spouse"
                    )
                    if rel_str:
                        lines.append("- СВЯЗИ: " + rel_str)
        except Exception:  # noqa: BLE001
            logger.debug("profile block failed", exc_info=True)

        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("player_block failed", exc_info=True)
        return ""


async def _season_block(session: AsyncSession) -> str:
    try:
        from app.repositories import season as season_repo

        season = await season_repo.get_active_season(session)
        if season is None:
            return "Сезон: сейчас межсезонье."
        name = getattr(season, "name", None) or f"#{season.id}"
        return f"Сезон: идёт «{name}» (id={season.id})."
    except Exception:  # noqa: BLE001
        logger.debug("season_block failed", exc_info=True)
        return ""


async def _events_block(session: AsyncSession, limit: int = 6) -> str:
    """Краткая сводка последних событий мира — ФОН, не главный материал.

    Намеренно компактно (6 строк): события — это приправа, а не суть разговора.
    Друн не должен в каждой реплике пересказывать ленту дуэлей.
    """
    try:
        rows = (
            await session.execute(
                select(WorldEvent)
                .order_by(WorldEvent.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        if not rows:
            return ""
        names = await resolve_names(
            session, [e.actor_id for e in rows] + [e.target_id for e in rows]
        )
        lines = ["Фоном в мире (можешь упомянуть, если в тему, но не пересказывай):"]
        for ev in rows:
            amount = f" ({money(ev.amount)})" if ev.amount else ""
            who = f" {name_for(names, ev.actor_id)}" if ev.actor_id else ""
            tgt = f" → {name_for(names, ev.target_id)}" if ev.target_id else ""
            lines.append(f"- [{ev.type}]{who}{tgt}{amount}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("events_block failed", exc_info=True)
        return ""


async def _overview_block(session: AsyncSession) -> str:
    """Общая картина чата с коротким TTL-кэшем.

    Это глобальный, медленно меняющийся срез (топы/суммы) — но раньше он гонял
    ~9 агрегатов по всей базе на КАЖДЫЙ ответ. Кэшируем на ``_OVERVIEW_TTL`` сек,
    как governor/config: всплеск реплик переиспользует один снимок.
    """
    import time as _t

    global _overview_cache
    now = _t.monotonic()
    if _overview_cache is not None and now - _overview_cache[0] < _OVERVIEW_TTL:
        return _overview_cache[1]
    block = await _overview_block_uncached(session)
    _overview_cache = (now, block)
    return block


_OVERVIEW_TTL = 60.0
_overview_cache: tuple[float, str] | None = None


_ECONOMY_TTL = 120.0
_economy_cache: tuple[float, str] | None = None


async def _economy_block(session: AsyncSession) -> str:
    """Экономическое чутьё: потоки ешек в чате (эмиссия/сток/инфляция).

    Дорогой агрегат по transactions — кэшируем на ``_ECONOMY_TTL`` сек (как
    overview). Друн видит ДВИЖЕНИЕ денег, а не статичные балансы, и может вести
    себя как хозяин казны: комментировать жадность казино, активность фарма, etc.
    """
    import time as _t

    global _economy_cache
    now = _t.monotonic()
    if _economy_cache is not None and now - _economy_cache[0] < _ECONOMY_TTL:
        return _economy_cache[1]
    from app.features.drun import economy as drun_economy

    block = await drun_economy.chat_economy_digest(session, hours=24)
    _economy_cache = (now, block)
    return block


_WORLDVIEW_TTL = 180.0
_worldview_cache: tuple[float, str] | None = None


async def _worldview_block(session: AsyncSession) -> str:
    """Убеждения и летопись друна (сюжеты/прогнозы/легенды), кэш на 3 мин.

    Друн ссылается на собственную историю мира — это и делает его живой
    сущностью с памятью дуг, а не реактивным ботом. Меняется медленно (дума
    раз в часы), поэтому кэшируем агрессивно.
    """
    import time as _t

    global _worldview_cache
    now = _t.monotonic()
    if _worldview_cache is not None and now - _worldview_cache[0] < _WORLDVIEW_TTL:
        return _worldview_cache[1]
    from app.features.drun import worldview as drun_worldview

    block = await drun_worldview.worldview_block(session)
    _worldview_cache = (now, block)
    return block


async def _overview_block_uncached(session: AsyncSession) -> str:
    """Общая картина чата: топы, богачи, бойцы, семьи, активные болтуны.

    Это «что вообще происходит у нас» — широкий срез базы, чтобы друн владел
    обстановкой и мог переключаться с темы на тему, а не упирался в дуэли.
    """
    try:
        from sqlalchemy import func

        lines: list[str] = ["# ОБЩАЯ КАРТИНА ЧАТА (для эрудиции, не пересказывай списком):"]

        # Сколько народу всего и сколько активных болтунов.
        total_users = await session.scalar(select(func.count()).select_from(User))
        if total_users:
            lines.append(f"- Всего жителей: {total_users}")

        # Топ-3 богача по балансу.
        rich = (
            await session.execute(
                select(User.user_id, User.balance)
                .order_by(User.balance.desc())
                .limit(3)
            )
        ).all()
        if rich:
            rnames = await resolve_names(session, [r[0] for r in rich])
            top = ", ".join(
                f"{name_for(rnames, uid)} ({money(bal)})" for uid, bal in rich
            )
            lines.append(f"- Богачи по ешкам: {top}")

        # Топ-3 болтуна (самые активные в чате).
        chatty = (
            await session.execute(
                select(User.user_id, User.messages_count)
                .order_by(User.messages_count.desc())
                .limit(3)
            )
        ).all()
        if chatty:
            cnames = await resolve_names(session, [r[0] for r in chatty])
            top = ", ".join(
                f"{name_for(cnames, uid)} ({cnt} сообщ.)" for uid, cnt in chatty
            )
            lines.append(f"- Самые болтливые: {top}")

        # Сколько семей в чате.
        try:
            from app.repositories import marriages as marr_repo

            married = await marr_repo.get_married_user_ids(session)
            if married:
                lines.append(f"- В браках состоит: {len(married)} чел.")
        except Exception:  # noqa: BLE001
            logger.debug("overview marriages failed", exc_info=True)

        # Топ-3 по MMR — кто короли рейтинга.
        try:
            from app.repositories import mmr as mmr_repo

            top_mmr = await mmr_repo.top_by_mmr(session, 3)
            if top_mmr:
                mnames = await resolve_names(session, [r.user_id for r in top_mmr])
                parts = [
                    f"{name_for(mnames, r.user_id)} ({r.mmr})"
                    for r in top_mmr if r.mmr
                ]
                if parts:
                    lines.append(f"- Короли MMR: {', '.join(parts)}")
        except Exception:  # noqa: BLE001
            logger.debug("overview mmr failed", exc_info=True)

        # ПРИМЕЧАНИЕ: «всего ешек в обороте» намеренно УБРАНО из общего контекста.
        # Это макро-цифра инфляции, почти никогда не относящаяся к живому разговору;
        # она лишь усиливала крен друна в «у кого сколько ешек» вместо разговора о
        # людях. Если нужно денежное чутьё — есть отдельный _economy_block (потоки),
        # а точные суммы друн добирает директивой [[ask:...]].

        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:  # noqa: BLE001
        logger.debug("overview_block failed", exc_info=True)
        return ""


async def _memory_block(
    session: AsyncSession,
    subject_id: int | None,
    query: str | None = None,
    channel: str = "chat",
) -> str:
    try:
        block, _ = await drun_memory_recall.build_recall(
            session,
            subject_id=subject_id,
            query=query,
            channel=channel,
        )
        return block
    except Exception:  # noqa: BLE001
        logger.debug("memory_block failed", exc_info=True)
        return ""


async def _archive_block(
    session: AsyncSession,
    subject_id: int | None,
    query: str | None = None,
) -> str:
    try:
        if not (query or "").strip():
            return ""
        block, _ = await drun_chat_archive.build_archive(
            session,
            subject_id=subject_id,
            query=query,
            channel="chat",
            limit=6,
        )
        return block
    except Exception:  # noqa: BLE001
        logger.debug("archive_block failed", exc_info=True)
        return ""


async def _identity_block(session: AsyncSession, query: str | None = None) -> str:
    try:
        return await drun_identity.build_identity_block(session, query)
    except Exception:  # noqa: BLE001
        logger.debug("identity_block failed", exc_info=True)
        return ""


# Человекочитаемые метки вложений для контекста (друн «видит» форму активности).
_MEDIA_RU: dict[str, str] = {
    "photo": "фото", "sticker": "стикер", "gif": "гифка", "voice": "голосовуха",
    "video_note": "кружок", "video": "видео", "audio": "аудио",
    "document": "файл", "poll": "опрос", "dice": "кубик/слот",
    "contact": "контакт", "location": "геолокация",
}


async def _chat_block(session: AsyncSession, channel: str, limit: int = 24) -> str:
    """Свежая болтовня игроков в чате (кто что сказал) — по никам.

    ГЛАВНЫЙ материал для ответа: о чём реально говорят люди прямо сейчас. Берём
    широкое окно (24 реплики), чтобы Друн чувствовал беседу, а не одну фразу.
    """
    try:
        msgs = await drun_memory.recent_chat(session, channel=channel, limit=limit)
        if not msgs:
            return ""
        ids = [m.user_id for m in msgs]
        names = await resolve_names(session, ids)
        # Подсказки про пол/как-звать для ВСЕХ участников беседы (не только для
        # текущего собеседника) — чтобы друн не путал род и имена людей,
        # упомянутых в чате.
        hints = await resolve_person_hints(session, ids)
        lines = [
            "# ЖИВОЙ ЧАТ ПРЯМО СЕЙЧАС (снизу — самые свежие реплики).",
            "# Прочитай и пойми НАСТРОЕНИЕ и О ЧЁМ базар, прежде чем встревать:",
        ]
        for m in msgs:
            meta = m.meta or {}
            who = meta.get("name") or name_for(names, m.user_id)
            # Восприятие формы реплики: кому отвечал автор и было ли вложение —
            # чтобы друн видел НИТЬ беседы, а не плоскую ленту.
            prefix = ""
            if meta.get("reply_to_bot"):
                prefix = "[в ответ ТЕБЕ] "
            elif meta.get("reply_to"):
                ex = meta.get("reply_excerpt")
                prefix = (
                    f"[в ответ {meta['reply_to']}"
                    + (f" на «{ex}»" if ex else "")
                    + "] "
                )
            media = meta.get("media")
            media_tag = f"[{_MEDIA_RU.get(media, media)}] " if media else ""
            lines.append(f"{who}: {prefix}{media_tag}{m.content}")
        # Кто есть кто (пол/имя) — компактным списком, без засорения каждой строки.
        who_is_who = [
            f"{name_for(names, uid)} ({hints[uid]})"
            for uid in dict.fromkeys(ids)
            if uid in hints
        ]
        if who_is_who:
            lines.append(
                "# КТО ЕСТЬ КТО (пол/как звать, не перепутай): "
                + "; ".join(who_is_who[:12])
            )
        lines.append("# (последняя строка выше — самое свежее в чате)")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("chat_block failed", exc_info=True)
        return ""


async def _antirepeat_block(session: AsyncSession, channel: str) -> str:
    """Последние собственные реплики друна — чтобы он НЕ повторялся.

    Друн склонен долбить одни зачины («бедыч», «ам ам», «бэм бэм») и одни
    жертвы. Показываем ему его свежие реплики с прямым запретом копировать
    структуру/обороты/мишени.
    """
    try:
        posts = await drun_memory.recent_self_posts(session, channel=channel, limit=6)
        if not posts:
            return ""
        lines = [
            "# ТЫ НЕДАВНО УЖЕ ПИСАЛ ЭТО (НЕ ПОВТОРЯЙСЯ!):",
            "# Не начинай так же, не используй те же обороты, приколы и "
            "слова-паразиты, не долби одну и ту же жертву. Звучи по-новому.",
        ]
        for p in posts:
            short = p if len(p) <= 160 else p[:159] + "…"
            lines.append(f"- {short}")
        # Умный анти-повтор: явный стоп-лист зажёванных зачинов/оборотов.
        try:
            from app.features.drun import antirepeat as ar_mod

            stop = ar_mod.render_block(posts)
            if stop:
                lines.append(stop)
        except Exception:  # noqa: BLE001
            logger.debug("antirepeat stop-list failed", exc_info=True)
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("antirepeat_block failed", exc_info=True)
        return ""


async def _now_block() -> str:
    """Текущая дата/время — чтобы друн ориентировался во времени, а не висел вне его."""
    from datetime import timezone, timedelta

    try:
        # Москва (UTC+3) — основная аудитория чата.
        now = now_utc().astimezone(timezone(timedelta(hours=3)))
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        months = [
            "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря",
        ]
        dow = days[now.weekday()]
        part = (
            "глубокая ночь" if now.hour < 5 else "утро" if now.hour < 12
            else "день" if now.hour < 18 else "вечер"
        )
        return (
            "# СЕЙЧАС (реальное время, ты живёшь во времени):\n"
            f"- {dow}, {now.day} {months[now.month - 1]} {now.year}, "
            f"{now.hour:02d}:{now.minute:02d} по мск ({part})."
        )
    except Exception:  # noqa: BLE001
        logger.debug("now_block failed", exc_info=True)
        return ""


async def _vibe_block(session: AsyncSession, channel: str) -> str:
    """Живой вайб чата прямо сейчас: насколько горячо и о чём.

    Не выдумка: считаем реальную активность за последние минуты и даём друну
    подсказку, какое сейчас настроение движа, чтобы он попадал в тон.
    """
    try:
        hot = await drun_memory.recent_chat_count(session, channel=channel, seconds=300)
        if hot >= 25:
            vibe = "ЧАТ КИПИТ — поток сообщений, все активны. Влетай дерзко и быстро."
        elif hot >= 10:
            vibe = "движ идёт — живая беседа. Держи темп, будь в потоке."
        elif hot >= 3:
            vibe = "вялый движ — пара человек переписывается. Без надрыва."
        else:
            vibe = "почти тишина — чат спит. Если влезать, то метко и не натужно."
        return f"# ВАЙБ ЧАТА: {vibe} (за 5 мин ~{hot} реплик)"
    except Exception:  # noqa: BLE001
        logger.debug("vibe_block failed", exc_info=True)
        return ""


async def _mood_block(session: AsyncSession, channel: str) -> str:
    """Текущее НАСТРОЕНИЕ друна (динамическое, #7) — красит тон ответа."""
    try:
        from app.features.drun import mood as drun_mood

        m = await drun_mood.compute_mood(session, channel=channel)
        return m.directive()
    except Exception:  # noqa: BLE001
        logger.debug("mood_block failed", exc_info=True)
        return ""


async def build_context(
    session: AsyncSession,
    *,
    subject_id: int | None = None,
    include_events: bool = True,
    channel: str = "chat",
    include_chat: bool = True,
    chat_limit: int = 24,
    query: str | None = None,
) -> str:
    """Собирает полный контекстный блок (всё, что друн «видит» сейчас).

    Порядок = приоритет внимания модели: сначала ВРЕМЯ и ВАЙБ, потом ДОСЬЕ на
    собеседника и ЖИВОЙ ЧАТ, потом СОЦИАЛЬНАЯ ПАМЯТЬ про людей и летопись, и лишь
    В САМОМ КОНЦЕ — денежно-макро фон (общая картина/экономика/сезон). Это
    сознательно: друн должен строить ответ на ЛЮДЯХ и их истории, а не сводить
    всё к «у кого больше ешек» — поэтому денежные блоки идут ниже социальных.

    ``chat_limit`` — сколько реплик чата подмешивать. Для прямого ответа человеку
    берём меньше (чтобы его сообщение не утонуло в логе), для автономного вкида —
    больше (друну нужно почувствовать беседу).

    ``query`` — текст текущей реплики собеседника. Если задан, блок ПАМЯТЬ
    ранжируется не только по весу/свежести, но и по релевантности этой теме —
    наверх всплывают воспоминания «в тему» разговора.
    """
    blocks: list[str] = [await _now_block()]
    route = classify_context_route(query, channel=channel, subject_id=subject_id)
    # Вайб чата осмыслен только когда мы вообще подмешиваем чат: для отчётов/
    # объявлений (include_chat=False) он не нужен — не тратим лишний COUNT-запрос.
    if include_chat and route.include_recent_chat:
        blocks.append(
            await _isolated(session, "vibe", lambda: _vibe_block(session, channel), "")
        )
        blocks.append(
            await _isolated(session, "mood", lambda: _mood_block(session, channel), "")
        )
    if subject_id is not None:
        blocks.append(
            await _isolated(
                session, "player", lambda: _player_block(session, subject_id), ""
            )
        )
    if include_chat:
        blocks.append(
            await _isolated(
                session,
                "chat",
                lambda: _chat_block(session, channel, limit=chat_limit),
                "",
            )
        )
    # IDENTITY: для запросов про людей/прошлое сначала резолвим, о ком речь,
    # чтобы модель не гадала по случайным фактам и не путала людей/пол.
    if route.include_identity and query:
        blocks.append(
            await _isolated(
                session, "identity", lambda: _identity_block(session, query), ""
            )
        )
    # ПАМЯТЬ про людей (факты/клички/связи) идёт СРАЗУ за живым чатом и досье —
    # это «социальное знание», на котором друн должен строить ответ. Раньше оно
    # тонуло НИЖЕ трёх денежных блоков (overview/economy/worldview), и модель,
    # видя больше про ешки, кренилась в «у кого сколько». Теперь соц-память выше
    # экономики; денежные блоки идут ФОНОМ в самом низу.
    memory_block, memory_ids = await _isolated(
        session,
        "memory",
        lambda: drun_memory_recall.build_recall(
            session, subject_id=subject_id, query=query, channel=channel,
        ),
        ("", []),
    )
    blocks.append(memory_block)
    archive_ids: list[int] = []
    if route.include_archive and route.archive_limit > 0:
        archive_block, archive_ids = await _isolated(
            session,
            "chat_archive",
            lambda: drun_chat_archive.build_archive(
                session,
                subject_id=subject_id,
                query=query,
                channel=channel,
                limit=route.archive_limit,
            ),
            ("", []),
        )
        blocks.append(archive_block)
    # web-факты подмешиваются отдельно в respond() (auto_context/grounded),
    # здесь route.include_web лишь помечает намерение и гасит лишние фоновые блоки.
    if route.include_worldview:
        blocks.append(
            await _isolated(session, "worldview", lambda: _worldview_block(session), "")
        )
    if include_events:
        blocks.append(
            await _isolated(session, "events", lambda: _events_block(session), "")
        )
    # Денежно-макро блоки — В САМОМ НИЗУ как фон (приправа, не суть): общая
    # картина чата и потоки экономики. Друн НЕ должен сводить разговор к ним.
    if route.include_overview:
        blocks.append(
            await _isolated(session, "overview", lambda: _overview_block(session), "")
        )
    if route.include_economy:
        blocks.append(
            await _isolated(session, "economy", lambda: _economy_block(session), "")
        )
        blocks.append(
            await _isolated(session, "season", lambda: _season_block(session), "")
        )
    blocks.append(
        await _isolated(
            session, "antirepeat", lambda: _antirepeat_block(session, channel), ""
        )
    )
    text = "\n\n".join(b for b in blocks if b).strip()
    return ContextBuildResult(text, memory_ids=memory_ids, archive_ids=archive_ids)
