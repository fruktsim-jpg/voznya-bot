"""Команды Тёмного друна.

``/друн`` (admin) — попросить друна бросить наблюдение в чат. MVP-триггер:
друн смотрит на мир/события и говорит в образе. Доступно только админам, чтобы
на старте контролировать включение и расход токенов.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.targets import resolve_target
from app.features.drun import econ as drun_econ
from app.features.drun import service as drun_service
from app.services import economy
from app.models import Transaction

router = Router(name="drun")


def _is_admin(message: Message) -> bool:
    return (
        message.from_user is not None
        and get_settings().is_admin(message.from_user.id)
    )


@router.message(RuCommand("друн", "drun", allow_no_prefix=False))
async def cmd_drun(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """/друн [@игрок] — друн бросает наблюдение (про мир или про игрока).

    ТОЛЬКО со слэшем (``/друн``): иначе любое сообщение, начинающееся со слова
    «друн» («друн как дела»), перехватывалось бы этой admin-командой и не
    доходило до reply-хендлера — друн либо молчал (не-админ), либо вкидывал
    монолог вместо ответа. Обращение по имени «друн ...» обрабатывает
    reply_handlers, а слэш-команда остаётся ручным admin-триггером.
    """
    if not _is_admin(message):
        return

    # Необязательная цель: /друн @user → наблюдение про конкретного игрока.
    subject_id: int | None = None
    if command_args.strip():
        target = await resolve_target(session, message, command_args)
        if target is not None:
            subject_id = target.user_id

    result = await drun_service.observe(session, subject_id=subject_id)
    if not result.ok:
        # Тихо для обычной работы; админу подскажем причину.
        if result.error == "disabled":
            await message.reply("Друн молчит: ИИ выключен или не настроен.")
        else:
            await message.reply(f"Друн поперхнулся: {result.error}")
        return

    # Сессию фиксирует DbSessionMiddleware после успешной обработки.
    # parse_mode=None: текст друна свободный, HTML-разметка сломала бы отправку.
    await message.answer(result.text, parse_mode=None)


@router.message(RuCommand("друноткат", "drunrollback", allow_no_prefix=False))
async def cmd_drun_rollback(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """/друноткат [N] — отменяет последние N эконом-выходок друна (по умолч. 5).

    Возвращает деньги: налог (списание) возвращается игроку, подачка (выдача)
    снимается обратно. Только админ. Безопасно: повторный откат уже отменённых
    операций ничего не сломает (помечаем в meta).
    """
    if not _is_admin(message):
        return

    n = 5
    arg = command_args.strip()
    if arg.isdigit():
        n = max(1, min(50, int(arg)))

    rows = (
        await session.execute(
            select(Transaction)
            .where(Transaction.reason.in_((drun_econ.REASON_TAX, drun_econ.REASON_GRANT)))
            .order_by(Transaction.created_at.desc())
            .limit(n)
        )
    ).scalars().all()

    reverted = 0
    returned = 0
    for tx in rows:
        if (tx.meta or {}).get("reverted"):
            continue
        # Обратная проводка: компенсируем исходную сумму со знаком минус.
        try:
            await economy.change_balance(
                session,
                tx.user_id,
                -tx.amount,
                "admin",
                {"action": "drun_rollback", "orig_tx": tx.id},
                allow_negative=True,
            )
        except Exception:  # noqa: BLE001
            continue
        tx.meta = {**(tx.meta or {}), "reverted": True}
        reverted += 1
        returned += abs(tx.amount)

    if reverted:
        await message.answer(
            f"Откатил {reverted} выходок друна, вернул движение на {returned} ешек."
        )
    else:
        await message.answer("Нечего откатывать — свежих выходок друна нет.")
