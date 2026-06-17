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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.money import money
from app.core.utils import now_utc
from app.features.drun import attitude as drun_attitude
from app.features.drun import memory as drun_memory
from app.features.drun.names import name_for, resolve_names
from app.models import User, WorldEvent

logger = get_logger(__name__)


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

        # Расширенная видимость: инвентарь/ачивки/сезон/модерация/кейсы.
        try:
            lines.extend(await _player_assets_block(session, user_id))
        except Exception:  # noqa: BLE001
            logger.debug("assets block failed", exc_info=True)

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
                gender = (pdata.get("gender") or "unknown").strip()
                if gender == "male":
                    lines.append("- ПОЛ: мужской (говори о нём в мужском роде)")
                elif gender == "female":
                    lines.append("- ПОЛ: женский (говори о ней в женском роде, "
                                 "это девушка — не лажай с родом)")
                if prof.summary:
                    lines.append(f"- ЛИЧНОСТЬ: {prof.summary}")
                if prof.speech_style:
                    lines.append(f"- МАНЕРА РЕЧИ: {prof.speech_style}")
                self_facts = pdata.get("self_facts") or []
                if self_facts:
                    lines.append("- САМ О СЕБЕ РАССКАЗЫВАЛ (помни это): "
                                 + "; ".join(self_facts[:8]))
                traits = pdata.get("traits") or []
                if traits:
                    lines.append("- ЧЕРТЫ: " + "; ".join(traits[:5]))
                topics = pdata.get("topics") or []
                if topics:
                    lines.append("- ЧАСТО ПРО: " + ", ".join(topics[:5]))
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

        # Экономика: сумма ешек на руках у всех — пульс инфляции.
        try:
            total_eshki = await session.scalar(
                select(func.coalesce(func.sum(User.balance), 0))
            )
            if total_eshki:
                lines.append(f"- Всего ешек в обороте: {money(int(total_eshki))}")
        except Exception:  # noqa: BLE001
            logger.debug("overview economy failed", exc_info=True)

        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:  # noqa: BLE001
        logger.debug("overview_block failed", exc_info=True)
        return ""


async def _memory_block(
    session: AsyncSession, subject_id: int | None, query: str | None = None
) -> str:
    try:
        mems = await drun_memory.scored_memories(
            session, subject_id=subject_id, query=query, limit=24
        )
        if not mems:
            return ""
        lines = ["Что ты помнишь про людей и мир (используй для подколов и связей):"]
        for m in mems:
            lines.append(f"- {m.fact}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("memory_block failed", exc_info=True)
        return ""


async def _chat_block(session: AsyncSession, channel: str, limit: int = 24) -> str:
    """Свежая болтовня игроков в чате (кто что сказал) — по никам.

    ГЛАВНЫЙ материал для ответа: о чём реально говорят люди прямо сейчас. Берём
    широкое окно (24 реплики), чтобы Друн чувствовал беседу, а не одну фразу.
    """
    try:
        msgs = await drun_memory.recent_chat(session, channel=channel, limit=limit)
        if not msgs:
            return ""
        names = await resolve_names(session, [m.user_id for m in msgs])
        lines = [
            "# ЖИВОЙ ЧАТ ПРЯМО СЕЙЧАС (снизу — самые свежие реплики).",
            "# Прочитай и пойми НАСТРОЕНИЕ и О ЧЁМ базар, прежде чем встревать:",
        ]
        for m in msgs:
            who = (m.meta or {}).get("name") or name_for(names, m.user_id)
            lines.append(f"{who}: {m.content}")
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
    собеседника и ЖИВОЙ ЧАТ, потом ПАМЯТЬ про людей, и лишь в конце — фон.

    ``chat_limit`` — сколько реплик чата подмешивать. Для прямого ответа человеку
    берём меньше (чтобы его сообщение не утонуло в логе), для автономного вкида —
    больше (друну нужно почувствовать беседу).

    ``query`` — текст текущей реплики собеседника. Если задан, блок ПАМЯТЬ
    ранжируется не только по весу/свежести, но и по релевантности этой теме —
    наверх всплывают воспоминания «в тему» разговора.
    """
    blocks: list[str] = [await _now_block()]
    # Вайб чата осмыслен только когда мы вообще подмешиваем чат: для отчётов/
    # объявлений (include_chat=False) он не нужен — не тратим лишний COUNT-запрос.
    if include_chat:
        blocks.append(await _vibe_block(session, channel))
        blocks.append(await _mood_block(session, channel))
    if subject_id is not None:
        blocks.append(await _player_block(session, subject_id))
    if include_chat:
        blocks.append(await _chat_block(session, channel, limit=chat_limit))
    blocks.append(await _memory_block(session, subject_id, query))
    blocks.append(await _overview_block(session))
    blocks.append(await _season_block(session))
    if include_events:
        blocks.append(await _events_block(session))
    blocks.append(await _antirepeat_block(session, channel))
    return "\n\n".join(b for b in blocks if b).strip()
