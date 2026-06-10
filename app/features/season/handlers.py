"""Хендлеры сезонной системы.

Команды игрока:
* «сезон» / «season» — карточка сезона: season MMR, дивизион, дни до конца;
* «бонус» / «daily» / «дейли» — ежедневная награда (раз в день), двигает стрик;
* «миссии» / «missions» — прогресс недельных заданий;
* «топсезон» / «topseason» — топ по сезонному MMR.

Начисление season MMR происходит автоматически в award_mmr (см. mmr.service).
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import open_on_site
from app.core.responses import notify_and_cleanup
from app.core.utils import display_name, place_marker
from app.features.season import service as season_service
from app.repositories import season as season_repo
from app.services.deletion import get_deletion_service
from app.settings import season as cfg

router = Router(name="season")


def _is_admin(message: Message) -> bool:
    """True, если автор сообщения — админ (по конфигу)."""
    return (
        message.from_user is not None
        and get_settings().is_admin(message.from_user.id)
    )



@router.message(RuCommand("сезон", "season"))
async def cmd_season(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Карточка сезона: сезонный MMR, дивизион, сколько дней осталось."""
    user = message.from_user
    if user is None:
        return

    active = await season_repo.get_active_season(session)
    if active is None:
        await notify_and_cleanup(
            session,
            message,
            "🗓 Сейчас межсезонье. Сезон скоро стартует — следи за анонсами.",
        )
        return

    season_mmr = await season_repo.get_season_mmr(session, user.id)
    div = cfg.get_division(season_mmr)
    days_left = max(
        0, (active.ends_at - datetime.now(timezone.utc)).days
    )
    season_url = f"{get_settings().website_url}/season"
    sent = await message.answer(
        cfg.SEASON_CARD.format(
            season_name=active.name,
            season_mmr=season_mmr,
            div_emoji=div.emoji,
            div_name=div.name,
            days_left=days_left,
        ),
        reply_markup=open_on_site("🗓 Сезон на сайте", season_url),
    )
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=user.id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
        ttl_seconds=180,
    )


@router.message(RuCommand("бонус", "daily", "дейли"))
async def cmd_daily(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Ежедневная награда. Раз в календарный день; двигает серию заходов."""
    user = message.from_user
    if user is None:
        return

    result = await season_service.claim_daily(session, user.id)
    if result.already:
        await notify_and_cleanup(session, message, cfg.DAILY_ALREADY)
        return
    await message.answer(
        cfg.DAILY_CLAIMED.format(amount=result.amount, streak=result.streak)
    )


@router.message(RuCommand("миссии", "missions"))
async def cmd_missions(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Показывает прогресс недельных заданий игрока."""
    user = message.from_user
    if user is None:
        return

    week = season_service.week_start(season_service.today_utc())
    rows = await season_repo.get_week_missions(
        session, user_id=user.id, week_start=week
    )
    progress_by_code = {r.mission_code: r for r in rows}

    lines = ["📋 <b>Недельные задания</b>\n"]
    for mission in cfg.WEEKLY_MISSIONS:
        row = progress_by_code.get(mission.code)
        done = row is not None and row.claimed_at is not None
        cur = min(row.progress, mission.target) if row else 0
        mark = "✅" if done else f"{cur}/{mission.target}"
        lines.append(
            f"{mark} {mission.title} — +{mission.reward_eshki} ешек, "
            f"+{mission.reward_mmr} MMR"
        )
    sent = await message.answer(
        "\n".join(lines),
        reply_markup=open_on_site("🗓 Миссии на сайте", f"{get_settings().website_url}/season"),
    )
    deletion = get_deletion_service()
    await deletion.schedule_info_message(
        session,
        user_id=user.id,
        chat_id=message.chat.id,
        user_command_id=message.message_id,
        bot_message_id=sent.message_id,
        ttl_seconds=180,
    )


@router.message(RuCommand("топсезон", "topseason"))
async def cmd_top_season(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Топ игроков по сезонному MMR."""
    top = await season_repo.top_by_season_mmr(session, 10)
    if not top:
        await notify_and_cleanup(
            session,
            message,
            "🏆 Сезонный топ пока пуст. Ферма, клады, дуэли и ачивки двигают тебя по сезону.",
        )
        return

    rows = "\n".join(
        f"{place_marker(i + 1)} {display_name(row.first_name, row.username)} — "
        f"{row.season_mmr:,} MMR ({cfg.get_division(row.season_mmr).name})"
        for i, row in enumerate(top)
    )
    await message.answer(
        f"🏆 <b>Сезонный топ</b>\n\n{rows}",
        reply_markup=open_on_site("🗓 Сезон на сайте", f"{get_settings().website_url}/season"),
    )


# --- Админские команды управления сезоном -----------------------------------


@router.message(RuCommand("стартсезон", "startseason"))
async def cmd_start_season(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Стартует новый сезон: /стартсезон <название> (только админ).

    Деактивирует текущий активный сезон (если был) и заводит новый на
    SEASON_LENGTH_DAYS дней. Сезонный MMR копится с нуля (вайп — отдельно).
    """
    if not _is_admin(message):
        return

    active = await season_repo.get_active_season(session)
    if active is not None:
        await message.answer(
            f"⚠️ Уже идёт сезон «{active.name}». Сначала заверши его "
            f"(/финалсезон)."
        )
        return

    name = command_args.strip() or "Сезон 1"
    season_id = await season_service.start_new_season(session, name=name)
    await message.answer(
        f"✅ Сезон «{name}» (#{season_id}) запущен на "
        f"{cfg.SEASON_LENGTH_DAYS} дней. Погнали!"
    )


@router.message(RuCommand("финалсезон", "finalizeseason"))
async def cmd_finalize_season(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Завершает активный сезон: выдаёт награды и титулы (только админ)."""
    if not _is_admin(message):
        return

    winners = await season_service.finalize_active_season(session)
    if not winners:
        await message.answer("🗓 Активного сезона нет — нечего завершать.")
        return

    lines = ["🏁 <b>Сезон завершён!</b> Награждаем:\n"]
    for w in winners[:10]:
        titles = (" " + " ".join(w.titles)) if w.titles else ""
        lines.append(
            f"{place_marker(w.rank)} id{w.user_id} — {w.division}, "
            f"{w.season_mmr:,} MMR{titles}"
        )
    await message.answer("\n".join(lines))


