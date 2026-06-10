"""Хендлеры системы репутации.

Три точки входа:

* ответ (reply) фразой-алиасом на чужое сообщение → +1 / -1 автору;
* команды «реп» / «репутация» → карточка репутации игрока;
* команда «топреп» → топ сообщества по репутации.

Репутация изолирована от ешек/XP/сообщений/магазина/инвентаря/Combot.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.keyboards import open_on_site, supports_web_app
from app.core.utils import display_name, format_cooldown, mention, place_marker
from app.features.reputation.service import apply_reputation, classify
from app.repositories import reputation as rep_repo
from app.services.deletion import get_deletion_service
from app.settings import reputation as rep_texts

router = Router(name="reputation")


class ReputationReply(BaseFilter):
    """Совпадает только с ответом-фразой репутации, иначе пропускает дальше.

    Узкий фильтр критичен: если бы хендлер ловил любой reply, aiogram считал
    бы апдейт обработанным и обычные ответы (и команды-в-ответ) не доходили бы
    до остальных роутеров. Здесь матчим лишь когда текст — точная фраза-алиас,
    и пробрасываем распознанный знак (+1/-1) в хендлер как ``rep_value``.
    """

    async def __call__(self, message: Message) -> bool | dict:
        if message.reply_to_message is None:
            return False
        value = classify(message.text or message.caption)
        if value is None:
            return False
        return {"rep_value": value}


@router.message(ReputationReply())
async def on_reputation_reply(
    message: Message, session: AsyncSession, rep_value: int
) -> None:
    """Меняет репутацию автору исходного сообщения по фразе-алиасу.

    Сюда попадают только ответы, где текст целиком — фраза-алиас (см.
    :class:`ReputationReply`). ``rep_value`` — уже распознанный знак (+1/-1).
    """
    giver = message.from_user
    reply = message.reply_to_message
    if giver is None or reply is None:
        return

    value = rep_value
    target = reply.from_user

    if target is None:
        await message.reply(rep_texts.REP_DELETED)
        return

    reason = " ".join((message.text or message.caption or "").lower().split())
    result = await apply_reputation(
        session,
        giver_user_id=giver.id,
        target_user_id=target.id,
        target_is_bot=bool(target.is_bot),
        value=value,
        reason=reason[:64] or None,
    )

    if result.status == "self":
        await message.reply(rep_texts.REP_SELF)
        return
    if result.status == "bot":
        await message.reply(rep_texts.REP_BOT)
        return
    if result.status == "deleted":
        await message.reply(rep_texts.REP_DELETED)
        return
    if result.status == "cooldown":
        await message.reply(
            rep_texts.REP_COOLDOWN.format(
                time=format_cooldown(result.retry_after_seconds)
            )
        )
        return

    who = mention(target.id, target.first_name, target.username)
    template = (
        rep_texts.REP_APPLIED_PLUS
        if result.value > 0
        else rep_texts.REP_APPLIED_MINUS
    )
    await message.reply(template.format(mention=who, score=result.new_score))


@router.message(RuCommand("реп", "репутация", "rep", "reputation"))
async def cmd_reputation(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Показывает карточку репутации: итог, плюсы, минусы.

    Без аргументов — своя репутация; в ответ на сообщение — репутация автора.
    """
    user = message.from_user
    if user is None:
        return

    target_id = user.id
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None:
        target_id = reply.from_user.id

    summary = await rep_repo.get_summary(session, target_id)
    sent = await message.answer(
        rep_texts.REP_CARD.format(
            score=summary.score, plus=summary.plus, minus=summary.minus
        ),
        reply_markup=open_on_site(
            "🏆 Топ репутации",
            f"{get_settings().website_url}/live",
            prefer_web_app=supports_web_app(message.chat.type),
        ),
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


@router.message(RuCommand("топреп", "toprep"))
async def cmd_top_reputation(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Показывает топ игроков по итоговой репутации."""
    top = await rep_repo.top_by_reputation(
        session, rep_texts.TOP_REPUTATION_LIMIT
    )
    if not top:
        await message.answer(
            rep_texts.REP_TOP_EMPTY,
            reply_markup=open_on_site(
                "🏆 Рейтинги на сайте",
                f"{get_settings().website_url}/live",
                prefer_web_app=supports_web_app(message.chat.type),
            ),
        )
        return

    rows = "\n".join(
        rep_texts.REP_TOP_ROW.format(
            place=place_marker(i + 1),
            mention=display_name(row.first_name, row.username),
            score=row.score,
        )
        for i, row in enumerate(top)
    )
    await message.answer(
        rep_texts.REP_TOP_HEADER.format(rows=rows),
        reply_markup=open_on_site(
            "🏆 Рейтинги на сайте",
            f"{get_settings().website_url}/live",
            prefer_web_app=supports_web_app(message.chat.type),
        ),
    )


