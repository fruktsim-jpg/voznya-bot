"""Сервис отложенного удаления сообщений (чистота чата).

Удаления сохраняются в БД и планируются через APScheduler, поэтому
переживают рестарт бота: при старте незавершённые задачи восстанавливаются.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.models import ScheduledDeletion

logger = get_logger(__name__)

# Глобальная ссылка на сервис (устанавливается при старте приложения).
_service: "DeletionService | None" = None


class DeletionService:
    """Планирует и выполняет отложенные удаления сообщений."""

    def __init__(
        self,
        bot: Bot,
        sessionmaker: async_sessionmaker[AsyncSession],
        scheduler: AsyncIOScheduler,
    ) -> None:
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.scheduler = scheduler
        # Кэш последних информационных сообщений: {(user_id, chat_id): (user_cmd_id, bot_msg_id)}
        self._last_info_messages: dict[tuple[int, int], tuple[int, int]] = {}
        self._last_leaderboard_messages: dict[tuple[int, str], tuple[int, int]] = {}

    async def schedule(
        self,
        session: AsyncSession,
        chat_id: int,
        message_id: int,
        delay_seconds: float,
    ) -> None:
        """Планирует удаление сообщения через ``delay_seconds`` секунд.

        Запись сохраняется в переданной сессии (коммит выполнит вызывающий код),
        а задача добавляется в планировщик немедленно.
        """
        delete_at = now_utc() + timedelta(seconds=delay_seconds)
        record = ScheduledDeletion(
            chat_id=chat_id, message_id=message_id, delete_at=delete_at
        )
        session.add(record)
        await session.flush()  # получаем record.id
        self._add_job(record.id, chat_id, message_id, delete_at)

    def _add_job(
        self, deletion_id: int, chat_id: int, message_id: int, delete_at: datetime
    ) -> None:
        """Добавляет задачу удаления в планировщик."""
        self.scheduler.add_job(
            self._execute,
            trigger="date",
            run_date=max(delete_at, now_utc() + timedelta(seconds=1)),
            args=[deletion_id, chat_id, message_id],
            id=f"del_{deletion_id}",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    async def _execute(self, deletion_id: int, chat_id: int, message_id: int) -> None:
        """Удаляет сообщение и помечает задачу выполненной."""
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest:
            # Сообщение уже удалено или недоступно — это нормально.
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось удалить сообщение %s: %s", message_id, exc)
        finally:
            await self._mark_done(deletion_id)

    async def _mark_done(self, deletion_id: int) -> None:
        async with self.sessionmaker() as session:
            record = await session.get(ScheduledDeletion, deletion_id)
            if record is not None:
                record.done = True
                await session.commit()

    async def schedule_info_message(
        self,
        session: AsyncSession,
        user_id: int,
        chat_id: int,
        user_command_id: int,
        bot_message_id: int,
        ttl_seconds: float | None = None,
    ) -> None:
        """Планирует удаление информационного сообщения.
        
        Автоматически удаляет предыдущую пару (команда + ответ)
        этого пользователя в этом чате. Если передан ``ttl_seconds``, новая пара
        также удалится по таймеру, не дожидаясь следующей информационной карточки.
        """
        key = (user_id, chat_id)
        prev_pair = self._last_info_messages.get(key)
        
        if prev_pair:
            prev_user_cmd, prev_bot_msg = prev_pair
            # Удаляем предыдущую команду пользователя
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=prev_user_cmd)
            except Exception:  # noqa: BLE001
                pass
            # Удаляем предыдущий ответ бота
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=prev_bot_msg)
            except Exception:  # noqa: BLE001
                pass
        
        # Сохраняем новую пару
        self._last_info_messages[key] = (user_command_id, bot_message_id)

        if ttl_seconds is not None and ttl_seconds > 0:
            await self.schedule(session, chat_id, user_command_id, ttl_seconds)
            await self.schedule(session, chat_id, bot_message_id, ttl_seconds)

    async def replace_leaderboard_message(
        self,
        chat_id: int,
        leaderboard_type: str,
        user_command_id: int,
        bot_message_id: int,
    ) -> None:
        """Deletes the previous leaderboard window of the same type in a chat."""
        key = (chat_id, leaderboard_type)
        prev_pair = self._last_leaderboard_messages.get(key)

        if prev_pair:
            prev_user_cmd, prev_bot_msg = prev_pair
            for message_id in (prev_user_cmd, prev_bot_msg):
                try:
                    await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:  # noqa: BLE001
                    pass

        self._last_leaderboard_messages[key] = (user_command_id, bot_message_id)

    async def restore_pending(self) -> None:
        """Восстанавливает незавершённые удаления после рестарта бота."""
        async with self.sessionmaker() as session:
            result = await session.execute(
                select(ScheduledDeletion).where(ScheduledDeletion.done.is_(False))
            )
            pending = result.scalars().all()

        count = 0
        for record in pending:
            self._add_job(
                record.id, record.chat_id, record.message_id, record.delete_at
            )
            count += 1
        if count:
            logger.info("Восстановлено отложенных удалений: %s", count)


def init_deletion_service(
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    scheduler: AsyncIOScheduler,
) -> DeletionService:
    """Создаёт и сохраняет глобальный сервис удалений."""
    global _service
    _service = DeletionService(bot, sessionmaker, scheduler)
    return _service


def get_deletion_service() -> DeletionService:
    """Возвращает глобальный сервис удалений."""
    if _service is None:
        raise RuntimeError("DeletionService не инициализирован")
    return _service
