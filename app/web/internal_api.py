"""Внутренний HTTP-API бота для сайта (Mini App / веб-витрина).

Маленький aiohttp-сервер, который поднимается РЯДОМ с polling-ботом и служит
ОДНОЙ цели: дать сайту вызвать существующую серверную логику бота, не дублируя
её на TypeScript. Сейчас здесь один эндпоинт — открытие кейса.

Почему так, а не «переписать на сайте»:
* ``open_case`` уже инкапсулирует CSPRNG-выбор, блокировки строк, списание
  ешек через экономическое ядро, инкремент лимитов, выдачу награды (включая
  pending-конвейер Telegram Gifts/Premium) и леджер открытий. Любая копия этой
  логики на сайте неизбежно разъедется и сломает честность/экономику;
* поэтому сайт остаётся «тонким»: проверяет свою player-сессию и проксирует
  запрос сюда, а вся мутация идёт там же, где и для бота.

Безопасность: эндпоинт НЕ публичный. Доступ только по общему секрету
(``X-Internal-Secret``), сервер слушает внутренний адрес (в docker-сети). Сайт
сам аутентифицирует игрока (подписанная сессия) и передаёт сюда уже доверенный
``user_id`` — наружу этот порт публиковать НЕЛЬЗЯ.

Транзакция: открываем сессию из общего ``sessionmaker`` и сами делаем
commit/rollback (здесь нет DbSessionMiddleware). Это та же гарантия атомарности,
что даёт middleware в боте.
"""

from __future__ import annotations

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.features.cases.events import CaseOpenEvent, emit_case_opened
from app.features.cases.service import open_case
from app.repositories import cases as cases_repo

logger = get_logger(__name__)

# HTTP-статусы для каждого исхода open_case (бизнес-исходы — это НЕ 500).
_STATUS_HTTP = {
    "ok": 200,
    "not_found": 404,
    "inactive": 409,
    "no_key": 409,
    "not_enough": 402,  # Payment Required — не хватает ешек
    "empty": 409,
    "error": 500,
}


def _require_secret(request: web.Request) -> bool:
    """Сверяет общий секрет из заголовка. Без секрета в конфиге — запрещаем."""
    expected = request.app["internal_secret"]
    if not expected:
        return False
    return request.headers.get("X-Internal-Secret") == expected


async def _handle_open_case(request: web.Request) -> web.Response:
    """POST /internal/cases/open — открыть кейс игроком через сайт.

    Тело JSON: ``{"user_id": <int>, "case_item_code": "<str>"}``.
    ``user_id`` уже доверенный (сайт проверил свою сессию). Возвращает поля
    OpenResult для рендера и анимации на сайте.
    """
    if not _require_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid_json"}, status=400)

    user_id = body.get("user_id")
    case_item_code = body.get("case_item_code")
    if not isinstance(user_id, int) or user_id <= 0:
        return web.json_response({"error": "invalid_user_id"}, status=400)
    if not isinstance(case_item_code, str) or not case_item_code:
        return web.json_response({"error": "invalid_case_item_code"}, status=400)

    sessionmaker: async_sessionmaker[AsyncSession] = request.app["sessionmaker"]
    session: AsyncSession = sessionmaker()
    total_openings = 0
    try:
        result = await open_case(
            session, user_id=user_id, case_item_code=case_item_code
        )
        # Счётчик открытий читаем в ТОЙ ЖЕ сессии (до commit/close), чтобы
        # отдать событию корректное число. Best-effort: не валим открытие.
        if result.status == "ok":
            try:
                total_openings = await cases_repo.count_openings(session, user_id)
            except Exception:  # noqa: BLE001
                total_openings = 0
        # Коммитим сами (здесь нет DbSessionMiddleware). Та же атомарность.
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("web open_case failed for user=%s case=%s", user_id, case_item_code)
        return web.json_response({"status": "error"}, status=500)
    finally:
        await session.close()

    payload = {
        "status": result.status,
        "caseName": result.case_name,
        "rewardKind": result.reward_kind,
        "rewardItemCode": result.reward_item_code,
        "rewardItemName": result.reward_item_name,
        "rewardRarity": result.reward_rarity,
        "amount": result.amount,
        "qty": result.qty,
        "isJackpot": result.is_jackpot,
        "balance": result.balance,
        # Для gift/premium — ключ pending-доставки (выдаётся вручную через
        # тот же конвейер /gifts_pending → /gifts_done).
        "deliveryKey": result.delivery_key,
    }

    http_status = _STATUS_HTTP.get(result.status, 500)

    # Событие для достижений/ленты (best-effort, не влияет на ответ) — паритет
    # с ботовым хендлером открытия. Эмитим уже ПОСЛЕ коммита, без сессии.
    if result.status == "ok":
        try:
            await emit_case_opened(
                CaseOpenEvent(
                    user_id=user_id,
                    case_item_code=case_item_code,
                    reward_kind=result.reward_kind,
                    reward_item_code=result.reward_item_code,
                    reward_rarity=result.reward_rarity,
                    amount=result.amount,
                    is_jackpot=result.is_jackpot,
                    total_openings=total_openings,
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("emit_case_opened (web) failed", exc_info=True)

    return web.json_response(payload, status=http_status)



async def _handle_health(request: web.Request) -> web.Response:
    """GET /internal/health — проверка живости (без секрета)."""
    return web.json_response({"ok": True})


async def start_internal_api(
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    host: str,
    port: int,
    secret: str,
) -> web.AppRunner:
    """Поднимает внутренний aiohttp-сервер и возвращает runner для остановки.

    Вызывается из ``main`` параллельно polling'у. Если секрет пустой —
    сервер всё равно стартует, но все защищённые эндпоинты вернут 403
    (безопасный дефолт: не работаем без секрета).
    """
    app = web.Application()
    app["sessionmaker"] = sessionmaker
    app["bot"] = bot
    app["internal_secret"] = secret

    app.router.add_post("/internal/cases/open", _handle_open_case)
    app.router.add_get("/internal/health", _handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("Внутренний API бота слушает %s:%s", host, port)
    return runner
