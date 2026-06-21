"""Автономные ПРЕДЛОЖЕНИЯ ивентов владельцу (Phase 6 + Phase 4 мост).

Друн уже умеет:
* сам комментировать мир (``autonomous``) — но это только слова;
* создавать структурные ивенты (``events.create_event``) — но только по явной
  команде владельца через ``registry``/``agent``.

Здесь — связующее звено: друн САМ замечает, что в мире назрел повод для движа
(чат затух, серия побед, накопился конфликт, мемная волна), и ПРЕДЛАГАЕТ
владельцу запустить ивент. Он НЕ запускает его сам и НЕ двигает деньги — он
кладёт ``DrunProposal(tool="create_event", ...)`` в ту же очередь approval-flow,
что и высокоимпактные owner-команды. Владелец в личке отвечает «да N» — и ивент
создаётся ровно тем же ``registry.dispatch`` с его клампами (награда ≤5000,
≤3 активных, дедлайн ≤72ч) и аудитом.

Модель безопасности (Model 2, без исключений):
* никаких денег здесь не двигается — только текстовое предложение;
* исполнение — через существующий approval-flow и экономическое ядро;
* опт-ин: работает только при ``autonomous_enabled`` (как и автопостинг);
* антиспам: не плодим дубль, пока есть pending-предложение create_event, и
  молчим, если уже идёт максимум активных ивентов;
* сбой — тихий лог, мир не падает.

Детекция повода — ЧИСТАЯ функция от метрик (``choose_event_idea``), её легко
тестировать без БД/LLM. Оркестрация (``propose_event_if_warranted``) только
собирает метрики, зовёт чистую функцию и пишет предложение.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.core.logger import get_logger
from app.features.drun import config as drun_config
from app.features.drun import events as drun_events
from app.features.drun import owner as drun_owner
from app.models import DrunProposal

logger = get_logger(__name__)

# Сколько активных ивентов терпим, прежде чем вообще перестать предлагать новые
# (тот же потолок, что и в движке ивентов — не предлагаем то, что не создастся).
_MAX_ACTIVE = drun_events._MAX_ACTIVE

# Дефолтные параметры предлагаемых ивентов. Награда — КОНСЕРВАТИВНАЯ (владелец
# поднимет при желании), и всё равно клампится движком при создании.
_DEAD_CHAT_REWARD = 500
_STREAK_REWARD = 1000
_DEFAULT_TTL_HOURS = 6


@dataclass(frozen=True)
class EventIdea:
    """Готовая идея ивента для предложения владельцу.

    Поля совпадают с аргументами ``create_event`` в реестре, чтобы предложение
    исполнилось без трансформации: ``{tool: 'create_event', args: {...}}``.
    """

    signal: str          # код повода (для дедупа/лога): dead_chat / win_streak / ...
    kind: str            # challenge | prediction | mini | goal
    title: str
    body: str
    reward: int
    hours: int
    rationale: str       # зачем это владельцу (показывается в очереди)

    def to_args(self) -> dict:
        """Аргументы для ``create_event`` (registry)."""
        return {
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "reward": self.reward,
            "hours": self.hours,
        }


@dataclass(frozen=True)
class ChatSignals:
    """Снимок состояния мира, по которому решаем, нужен ли ивент."""

    msgs_window: int          # реплик игроков за окно пульса
    speakers_window: int      # уникальных авторов за окно
    top_farm_streak: int      # максимальная серия фарма в чате (живой «рекорд»)
    active_events: int        # сколько ивентов друна уже идёт


# Пороги детекции (вынесены в константы — видно и легко крутить).
_DEAD_MSGS = 2            # ≤ столько реплик за окно — чат мёртвый
_STREAK_MIN = 10          # серия фарма, с которой повод устроить челлендж


def choose_event_idea(signals: ChatSignals) -> EventIdea | None:
    """Чистая функция: по метрикам мира выбирает идею ивента (или None).

    Приоритет поводов (от сильного к слабому):
    1. кто-то держит заметную серию фарма → челлендж «перебей рекорд»;
    2. чат мёртвый → мини-ивент, чтобы вытащить людей.

    Возвращает None, если повода нет ИЛИ уже идёт максимум ивентов (не предлагаем
    то, что движок откажется создать).
    """
    if signals.active_events >= _MAX_ACTIVE:
        return None

    # 1) Серия фарма — наглядный «рекорд» в чате, вокруг которого строится
    #    челлендж. Работает даже в активном чате.
    if signals.top_farm_streak >= _STREAK_MIN:
        return EventIdea(
            signal="farm_streak",
            kind="challenge",
            title="Перебей серию",
            body=(
                f"В чате кто-то держит серию фарма из {signals.top_farm_streak} "
                "дней подряд. Челлендж: продержись дольше — забери банк."
            ),
            reward=_STREAK_REWARD,
            hours=_DEFAULT_TTL_HOURS,
            rationale=(
                f"серия фарма {signals.top_farm_streak} дней — наглядный повод "
                "для челленджа, пока тема свежая"
            ),
        )

    # 2) Мёртвый чат — вкидываем мини-ивент, чтобы расшевелить (но только если
    #    реально тихо: мало и реплик, и говорящих).
    if signals.msgs_window <= _DEAD_MSGS and signals.speakers_window <= 1:
        return EventIdea(
            signal="dead_chat",
            kind="mini",
            title="Оживи чат",
            body=(
                "В чате тишина. Мини-ивент: первые, кто вернётся и закинет "
                "сообщение/мем, получат ешки. Время пошло."
            ),
            reward=_DEAD_CHAT_REWARD,
            hours=_DEFAULT_TTL_HOURS,
            rationale="чат затух — ивент вытащит людей обратно",
        )

    return None


async def _has_pending_event_proposal(session: AsyncSession) -> bool:
    """Есть ли уже pending-предложение create_event (антидубль)."""
    count = await session.scalar(
        select(func.count())
        .select_from(DrunProposal)
        .where(DrunProposal.status == "pending")
        .where(DrunProposal.tool == "create_event")
    )
    return bool(count)


async def _top_farm_streak(session: AsyncSession) -> int:
    """Максимальная текущая серия фарма среди игроков (дёшево, один агрегат)."""
    from app.models import User

    val = await session.scalar(select(func.max(User.farm_streak)))
    return int(val or 0)


async def propose_event_if_warranted(
    session: AsyncSession, *, channel: str = "chat"
) -> DrunProposal | None:
    """Смотрит на мир и, если назрел повод, кладёт предложение ивента в очередь.

    Возвращает созданный ``DrunProposal`` (для уведомления владельца) или None,
    если повода нет / сработал предохранитель. Commit — на вызывающем.
    """
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return None
    # Тот же опт-ин, что и у автопостинга: друн не проявляет инициативу, пока
    # владелец явно не включил автономный режим.
    if not cfg.autonomous_enabled:
        return None

    # Антидубль: не плодим вторую заявку, пока владелец не разобрал первую.
    if await _has_pending_event_proposal(session):
        return None

    from app.features.drun import memory as drun_memory

    msgs, speakers = await drun_memory.pulse_stats(session, channel=channel)
    signals = ChatSignals(
        msgs_window=msgs,
        speakers_window=speakers,
        top_farm_streak=await _top_farm_streak(session),
        active_events=await drun_events.active_count(session),
    )
    idea = choose_event_idea(signals)
    if idea is None:
        return None

    # Кому предлагать: первому владельцу из ADMIN_IDS (личка approval-flow).
    admin_ids = get_settings().admin_ids
    owner_id = admin_ids[0] if admin_ids else None

    proposal = await drun_owner.create_proposal(
        session,
        owner_id=owner_id,
        tool="create_event",
        args=idea.to_args(),
        rationale=f"[авто] {idea.rationale}",
    )
    logger.info(
        "drun event proposal queued (#%s signal=%s kind=%s reward=%s)",
        proposal.id, idea.signal, idea.kind, idea.reward,
    )
    return proposal


def _format_proposal_dm(proposal: DrunProposal) -> str:
    """Текст уведомления владельцу в личку про новое предложение ивента."""
    args = proposal.args or {}
    title = args.get("title", "ивент")
    reward = args.get("reward", 0)
    reward_str = f", награда {reward} ешек" if reward else ""
    return (
        f"Есть идея для движа. Предложение #{proposal.id}: "
        f"ивент «{title}»{reward_str}.\n"
        f"{proposal.rationale}\n\n"
        f"Запускаю? «да {proposal.id}» — да, «нет {proposal.id}» — пропустить."
    )


def setup_event_proposer(
    scheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    minutes: int = 23,
) -> None:
    """Регистрирует фоновую джобу автономных предложений ивентов.

    Раз в ``minutes`` минут друн оценивает мир и, если назрел повод, кладёт
    предложение ивента в approval-очередь и пишет владельцу в личку. Сам ивент
    НЕ создаётся, деньги НЕ двигаются — всё через подтверждение владельца.

    Интервал намеренно нечастый (по умолчанию 23 мин) и со своим антидублем:
    одно pending-предложение create_event за раз — друн не заваливает владельца.
    """

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                proposal = await propose_event_if_warranted(session)
                await session.commit()
                # Считываем поля до закрытия сессии (объект истечёт).
                dm_text = _format_proposal_dm(proposal) if proposal else None
                owner_id = proposal.owner_id if proposal else None
            if not dm_text or owner_id is None:
                return
            from app.features.drun.presence import get_presence

            presence = get_presence()
            if presence is None:
                return
            await presence.say_dm(owner_id, dm_text)
            logger.info("drun event proposal: owner notified (dm=%s)", owner_id)
        except Exception:  # noqa: BLE001
            logger.warning("drun event proposer failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_event_proposer",
        replace_existing=True,
    )
