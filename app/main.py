"""Точка входа приложения: сборка и запуск Telegram-бота «Возня».

Здесь связываются все части:
* бот и диспетчер aiogram;
* middleware (сессия БД, фильтр чата, трекинг, антифлуд);
* роутеры всех игровых модулей;
* планировщик (клады, отложенные удаления);
* восстановление незавершённых задач после рестарта.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.logger import get_logger, setup_logging
from app.core.scheduler import get_scheduler, shutdown_scheduler, start_scheduler
from app.features import get_feature_routers
from app.features.treasure.service import setup_treasure_scheduler
from app.middlewares import (
    AntiFloodMiddleware,
    ChatFilterMiddleware,
    DbSessionMiddleware,
    UserTrackingMiddleware,
)
from app.services.deletion import init_deletion_service
from app.services.link_maintenance import setup_link_maintenance


logger = get_logger(__name__)

# Команды для меню Telegram. Telegram принимает только латиницу в названиях
# команд меню, поэтому здесь — латинские алиасы (русские команды работают
# в чате благодаря собственному фильтру RuCommand).
BOT_COMMANDS = [
    BotCommand(command="farm", description="💊 Ферма — поднять ешек"),
    BotCommand(command="balance", description="💰 Сколько у тебя ешек"),
    BotCommand(command="profile", description="👤 Твоя карточка"),
    BotCommand(command="achievements", description="🏅 Ачивки"),
    BotCommand(command="top", description="🏆 Богачи Возни"),
    BotCommand(command="weekly", description="📅 Богачи недели"),
    BotCommand(command="casino", description="🎰 Рискнуть в казино"),
    BotCommand(command="duel", description="⚔️ Звать на замес"),
    BotCommand(command="accept", description="⚔️ Принять бой"),
    BotCommand(command="claim", description="📦 Забрать клад"),
    BotCommand(command="pidor", description="🏳️‍🌈 Пидор дня"),
    BotCommand(command="couple", description="💞 Пара дня"),
    BotCommand(command="marry", description="💍 Предложение руки и сердца"),
    BotCommand(command="marriage", description="💍 Инфо о браке"),
    BotCommand(command="divorce", description="💔 Развод"),
    BotCommand(command="families", description="💞 Крепкие семьи"),
    BotCommand(command="help", description="❓ Помощь и команды"),

]


def create_dispatcher() -> Dispatcher:
    """Создаёт диспетчер, регистрирует middleware и роутеры."""
    dp = Dispatcher()
    sessionmaker = get_sessionmaker()

    # Сессия БД доступна для всех типов апдейтов.
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))

    # Цепочка обработки сообщений.
    dp.message.middleware(ChatFilterMiddleware())
    dp.message.middleware(UserTrackingMiddleware())
    dp.message.middleware(AntiFloodMiddleware())

    # Нажатия на кнопки: тот же фильтр чата и трекинг активности.
    dp.callback_query.middleware(ChatFilterMiddleware())
    dp.callback_query.middleware(UserTrackingMiddleware())

    for router in get_feature_routers():
        dp.include_router(router)

    return dp


async def on_startup(bot: Bot) -> None:
    """Действия при запуске: планировщик, восстановление задач, меню команд."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    scheduler = get_scheduler()

    deletion_service = init_deletion_service(bot, sessionmaker, scheduler)
    start_scheduler()

    # Восстанавливаем отложенные удаления, оставшиеся после рестарта.
    await deletion_service.restore_pending()

    # Планируем появления кладов.
    setup_treasure_scheduler(scheduler, bot, sessionmaker, settings.chat_id)

    # Периодически подчищаем протухшие запросы привязки сайта (OIDC).
    setup_link_maintenance(scheduler, sessionmaker)


    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось установить меню команд: %s", exc)

    me = await bot.get_me()
    logger.info("Бот @%s запущен. Целевой чат: %s", me.username, settings.chat_id)


async def main() -> None:
    """Главная корутина приложения."""
    settings = get_settings()
    setup_logging(settings.log_level)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()
    dp.startup.register(on_startup)

    try:
        await dp.start_polling(
            bot, allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        shutdown_scheduler()
        await dispose_engine()
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
