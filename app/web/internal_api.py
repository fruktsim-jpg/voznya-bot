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

from app.config import get_settings
from app.core.logger import get_logger
from app.features.cases.events import CaseOpenEvent, emit_case_opened
from app.features.cases.service import open_case
from app.features.gifts.service import deliver_gift
from app.repositories import cases as cases_repo
from app.repositories import gifts as gifts_repo


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



async def _handle_deliver_gift(request: web.Request) -> web.Response:
    """POST /internal/gifts/deliver — попытка АВТО-выдачи подарка (P2).

    Тело JSON: ``{"user_id": <int>, "delivery_key": "<idempotency_key>"}``.

    Сайт жмёт «Вывести» → сразу вызывает сюда, бот пытается выдать подарок
    через Telegram (``deliver_gift`` — тот же конвейер, что у кнопки в боте).
    Авто-выдача — ОСНОВНОЙ сценарий (aiogram 3.28+):

      success         → completed (подарок отправлен);
      temp-fail       → остаётся pending, фоновый воркер повторит (запасной путь);
      permanent-fail  → cancelled + возврат стоимости (логика внутри deliver_gift).

    Проверяем владельца: выдать можно только СВОЙ pending-подарок (защита от
    вывода чужого по подменённому ключу). user_id уже доверенный (сайт проверил
    сессию). Идемпотентно: deliver_gift берёт строку FOR UPDATE.
    """
    if not _require_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid_json"}, status=400)

    user_id = body.get("user_id")
    delivery_key = body.get("delivery_key")
    if not isinstance(user_id, int) or user_id <= 0:
        return web.json_response({"error": "invalid_user_id"}, status=400)
    if not isinstance(delivery_key, str) or not delivery_key:
        return web.json_response({"error": "invalid_delivery_key"}, status=400)

    bot: Bot = request.app["bot"]
    sessionmaker: async_sessionmaker[AsyncSession] = request.app["sessionmaker"]
    settings = get_settings()
    session: AsyncSession = sessionmaker()
    try:
        # Владелец: подарок должен принадлежать этому игроку и быть pending.
        delivery = await gifts_repo.get_delivery_for_update(session, delivery_key)
        if delivery is None or delivery.recipient_user_id != user_id:
            await session.rollback()
            return web.json_response({"status": "not_found"}, status=404)
        if delivery.status != "pending":
            # Уже обработан (выдан/отменён/продан) — не дублируем.
            await session.rollback()
            return web.json_response(
                {"status": "not_pending", "deliveryStatus": delivery.status},
                status=409,
            )

        outcome = await deliver_gift(
            session,
            bot,
            idempotency_key=delivery_key,
            enabled=settings.gifts_delivery_enabled,
            channel="site",
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("web deliver_gift failed for user=%s key=%s", user_id, delivery_key)
        return web.json_response({"status": "error"}, status=500)
    finally:
        await session.close()

    # completed → 200; pending (временная ошибка, повторит воркер) → 202;
    # cancelled (возврат) → 200; skip → 409.
    http_status = {
        "completed": 200,
        "pending": 202,
        "cancelled": 200,
        "skip": 409,
    }.get(outcome.status, 200)
    return web.json_response(
        {"status": outcome.status, "refunded": outcome.refunded, "error": outcome.error},
        status=http_status,
    )


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
    app.router.add_post("/internal/gifts/deliver", _handle_deliver_gift)
    app.router.add_get("/internal/health", _handle_health)


    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("Внутренний API бота слушает %s:%s", host, port)
    return runner
