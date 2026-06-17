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
from app.features.gifts.worker import setup_gift_delivery_worker
from app.features.moderation.enforcement import (
    MuteEnforcementMiddleware,
    setup_moderation_scheduler,
)
from app.features.treasure.service import setup_treasure_scheduler

from app.features.drun.ears import DrunEarsMiddleware
from app.features.drun.distill import setup_memory_distill
from app.features.drun.chat_memory import setup_chat_distill
from app.features.drun.profile import setup_profile_sweep

from app.middlewares import (
    AntiFloodMiddleware,
    ChatFilterMiddleware,
    DbSessionMiddleware,
    UserTrackingMiddleware,
)
from app.services.deletion import init_deletion_service
from app.services.link_maintenance import setup_link_maintenance
from app.web import start_internal_api



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
    BotCommand(command="gifts", description="🎁 Магазин подарков"),
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
    # «Уши» друна: пишем реплики игроков в память ИИ (после трекинга, чтобы
    # пользователь уже был апсертнут). Не влияет на доставку сообщений.
    dp.message.middleware(DrunEarsMiddleware())
    # Backstop-энфорсмент мьюта: удаляет сообщения замьюченных, если Telegram
    # сам не ограничил (например, у бота не было прав в момент команды).
    dp.message.middleware(MuteEnforcementMiddleware())
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

    # Авто-выдача подарков, выведенных игроком с сайта (P2): сайт помечает
    # доставку withdraw_requested, бот реально отправляет через Telegram.
    setup_gift_delivery_worker(scheduler, bot, sessionmaker)

    # Модерация: периодически снимает истёкшие баны/мьюты и обновляет кэш
    # активных мьютов для backstop-энфорсмента.
    setup_moderation_scheduler(scheduler, bot, sessionmaker, settings.chat_id)

    # Память друна: периодически дистиллируем события мира в устойчивые факты
    # об игроках и их взаимодействиях (дёшево, без LLM).
    setup_memory_distill(scheduler, sessionmaker)

    # Живая память: LLM-дистилляция тем разговоров, характеров и отношений из
    # чата (раз в 45 мин) — чтобы друн помнил людей, а не только статистику.
    setup_chat_distill(scheduler, sessionmaker)

    # Профили игроков: фоновый свип (раз в несколько минут) пересобирает досье
    # активных игроков из всей базы + LLM-портрет — почти реалтайм-память.
    setup_profile_sweep(scheduler, sessionmaker)



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

    # Внутренний HTTP-API для сайта (открытие кейсов через веб). Поднимается
    # рядом с polling, только если задан секрет — иначе пропускаем (бот живёт
    # как раньше). Это НЕ публичный порт: слушаем внутренний адрес docker-сети.
    api_runner = None
    if settings.internal_api_enabled and settings.internal_api_secret:
        api_runner = await start_internal_api(
            bot,
            get_sessionmaker(),
            host=settings.internal_api_host,
            port=settings.internal_api_port,
            secret=settings.internal_api_secret,
        )
    elif settings.internal_api_enabled:
        logger.warning(
            "INTERNAL_API_ENABLED=true, но INTERNAL_API_SECRET пуст — "
            "внутренний API не поднят (нужен секрет)."
        )

    try:
        await dp.start_polling(
            bot, allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        if api_runner is not None:
            await api_runner.cleanup()
        shutdown_scheduler()
        await dispose_engine()
        await bot.session.close()
        logger.info("Бот остановлен.")



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
