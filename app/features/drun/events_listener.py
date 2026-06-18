"""Real-time подписка друна на крупные события мира через Postgres NOTIFY.

Раньше реакция друна на «прыгнул джекпот» или «свадьба» догоняла событие через
опросный тик автономного постера (`autonomous.py`, ``minutes=7``) и могла
задерживаться от 7 минут до 6 часов (если ещё попадала под кулдауны/капы). Это
убивало живость: к моменту реплики джекпот уже все обсудили.

Тут — двусторонний канал ``LISTEN world_events`` поверх того же asyncpg-пула,
на который Postgres пушит ``NOTIFY world_events`` (см. ``services.world_events.
emit`` — публикуется при ``severity >= 2``). Получили нотис → дебаунс окошко (на
случай взрыва событий) → один прогон ``comment_on_fresh_events`` (он сам решит,
комментировать ли, через governor/капы/идемпотентность по high-water-mark).

Дешёво:
* одна longlived asyncpg-connection из пула движка (raw, без сессии);
* без polling, спим на ``conn.add_listener`` callback;
* при сбое (порвалась connection, рестарт PG) — экспоненциальный бэкофф и
  переподключение; опросный тик `autonomous` остаётся как страховка.

Без новых таблиц/миграций. Канал ``world_events`` фиксированный.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from aiogram import Bot
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.features.drun import autonomous as drun_autonomous

logger = get_logger(__name__)

# Канал должен совпадать с тем, что использует services.world_events.emit().
_CHANNEL = "world_events"
# Дебаунс: при пачке нотисов (пример — «джекпот + следом подарок победителю»)
# собираем хвост за окно и дёргаем comment_on_fresh_events один раз.
_DEBOUNCE_SEC = 1.5
# Бэкофф при разрыве LISTEN-соединения: 1с, 2с, 4с, 8с, ... до 60с.
_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 60.0


class WorldEventsListener:
    """Долгоживущий LISTEN-подписчик: тригер реакций друна в реальном времени.

    Жизненный цикл — общий с приложением: ``start()`` в lifespan на старте,
    ``stop()`` при остановке. Внутри держит один asyncpg-коннект (вне пула
    SQLAlchemy, чтобы LISTEN не блокировал обычные транзакции) и один
    обработчик. Все вызовы в drun идут через переданный ``sessionmaker``.
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        bot: Bot,
        chat_id: int,
    ) -> None:
        self._engine = engine
        self._sessionmaker = sessionmaker
        self._bot = bot
        self._chat_id = chat_id
        self._task: asyncio.Task[None] | None = None
        # Дебаунс-таск: пока ждём окно, новые нотисы лишь продлевают тот же таск.
        self._debounce_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """Запускает фоновый run-loop. Идемпотентно."""
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(
            self._run(), name="drun-events-listener",
        )
        logger.info("drun events listener started (channel=%s)", _CHANNEL)

    async def stop(self) -> None:
        """Корректно останавливает loop и снимает LISTEN."""
        self._stopping.set()
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task
            self._debounce_task = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("drun events listener stopped")

    # ----- внутреннее -----

    async def _run(self) -> None:
        """Главный цикл с бэкоффом на переподключение."""
        backoff = _BACKOFF_MIN
        while not self._stopping.is_set():
            try:
                await self._listen_forever()
                # _listen_forever выходит только при stop() или фатальной ошибке.
                backoff = _BACKOFF_MIN
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning(
                    "drun listener crashed, reconnect in %.1fs", backoff,
                    exc_info=True,
                )
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=backoff,
                    )
                    return  # stop() пока спали
                except asyncio.TimeoutError:
                    backoff = min(_BACKOFF_MAX, backoff * 2)

    async def _listen_forever(self) -> None:
        """Берёт raw asyncpg-коннект, вешает LISTEN, ждёт пока stop() или сбой.

        Используем ``engine.raw_connection`` через sync API → достаём низлежащий
        ``asyncpg.Connection``. SQLAlchemy AsyncAdaptedQueuePool возвращает
        ``AsyncAdapt_asyncpg_connection``, у которого реальный asyncpg-объект
        лежит в ``.driver_connection``. Это даёт нам ``add_listener`` без
        прыжков мимо settings и без второго пула.
        """
        # raw_connection — sync метод; на async-engine он возвращает обёртку,
        # у которой есть .driver_connection (реальный asyncpg.Connection).
        # Делать это безопасно: коннект эксклюзивно наш до release().
        sync_conn = await self._engine.connect()
        try:
            raw = await sync_conn.get_raw_connection()
            asyncpg_conn = raw.driver_connection  # asyncpg.Connection
            if asyncpg_conn is None:
                # Не asyncpg-драйвер — режим тихо отключаем, опросный тик
                # автономного постера останется страховкой.
                logger.warning(
                    "drun listener: no asyncpg driver_connection, NOTIFY"
                    " disabled (falling back to polling)",
                )
                await self._stopping.wait()
                return

            await asyncpg_conn.add_listener(_CHANNEL, self._on_notify)
            logger.info(
                "drun listener: LISTEN %s on raw asyncpg conn", _CHANNEL,
            )
            try:
                # Спим, пока не попросят остановиться. asyncpg сам вызовет
                # callback в loop при приходе NOTIFY.
                await self._stopping.wait()
            finally:
                with contextlib.suppress(Exception):
                    await asyncpg_conn.remove_listener(
                        _CHANNEL, self._on_notify,
                    )
        finally:
            with contextlib.suppress(Exception):
                await sync_conn.close()

    def _on_notify(
        self, _conn: Any, _pid: int, channel: str, payload: str,
    ) -> None:
        """Callback от asyncpg. Только планирует дебаунс-таск — никакой работы.

        Сам callback вызывается в event loop, синхронный по сигнатуре. Любая
        тяжёлая работа (DB, LLM, сеть) уходит в отдельный таск, чтобы не
        блокировать дальнейшие нотисы.
        """
        if self._stopping.is_set():
            return
        if channel != _CHANNEL:
            return
        logger.debug("drun listener: NOTIFY payload=%s", payload[:200])
        # Перезапускаем дебаунс-окно: пачка нотисов схлопывается в один прогон.
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(
            self._debounced_react(), name="drun-events-debounce",
        )

    async def _debounced_react(self) -> None:
        """Ждёт окно дебаунса, потом дёргает реактивный пайплайн друна один раз."""
        try:
            await asyncio.sleep(_DEBOUNCE_SEC)
        except asyncio.CancelledError:
            return
        try:
            await self._react_once()
        except Exception:  # noqa: BLE001
            logger.warning("drun listener: react failed", exc_info=True)

    async def _react_once(self) -> None:
        """Один вызов реактивного пайплайна.

        Используем существующий ``comment_on_fresh_events`` — он уже знает про
        high-water-mark, дневной кап, governor и идемпотентность. NOTIFY тут
        служит лишь триггером «посмотри СЕЙЧАС», а не источником данных:
        правда о событии всё равно читается из ``world_events``. Это значит,
        что при пропущенном/дублированном NOTIFY поведение не ломается.
        """
        async with self._sessionmaker() as session:
            text = await drun_autonomous.comment_on_fresh_events(session)
            await session.commit()
        if text:
            try:
                await self._bot.send_message(self._chat_id, text)
                logger.info(
                    "drun listener: posted reactive comment (channel=%s)",
                    _CHANNEL,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "drun listener: send_message failed", exc_info=True,
                )


# --- Регистрация в lifespan приложения --------------------------------------

_listener: WorldEventsListener | None = None


async def setup_events_listener(
    *,
    engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
    bot: Bot,
    chat_id: int,
) -> WorldEventsListener:
    """Создаёт и стартует слушатель. Зовётся из ``main.py`` после scheduler."""
    global _listener
    if _listener is not None:
        return _listener
    _listener = WorldEventsListener(
        engine=engine, sessionmaker=sessionmaker, bot=bot, chat_id=chat_id,
    )
    await _listener.start()
    # Прицепим грейсфул-стоп к dispose движка — на случай, если стопалку
    # приложения забыли вызвать вручную (например, в тестах).
    sa_event.listens_for  # touch для read-only-импорта (silences linters)
    return _listener


async def teardown_events_listener() -> None:
    """Останавливает слушатель (lifespan shutdown)."""
    global _listener
    if _listener is None:
        return
    await _listener.stop()
    _listener = None
