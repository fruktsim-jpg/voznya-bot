"""Игровые механики, оформленные как независимые модули.

Каждый модуль предоставляет свой ``router`` (aiogram Router). Чтобы добавить
новую механику (магазин, достижения и т.п.), достаточно создать новый
подпакет с роутером и зарегистрировать его в :func:`get_feature_routers`.
"""

from __future__ import annotations

from aiogram import Router


def get_feature_routers() -> list[Router]:
    """Возвращает список роутеров всех игровых модулей.

    Порядок важен только для пересекающихся фильтров; команды уникальны,
    поэтому порядок здесь — логический (от частого к редкому).
    """
    from app.features.achievements.handlers import router as achievements_router
    from app.features.admin.handlers import router as admin_router
    from app.features.balance.handlers import router as balance_router
    from app.features.cases.handlers import router as cases_router
    from app.features.casino.handlers import router as casino_router

    from app.features.duel.handlers import router as duel_router
    from app.features.farm.handlers import router as farm_router
    from app.features.gifts.claim_handlers import router as gift_claim_router
    from app.features.gifts.handlers import router as gifts_router


    from app.features.help.handlers import router as help_router
    from app.features.inventory.handlers import router as inventory_router
    from app.features.linking.handlers import router as linking_router
    from app.features.marriage.handlers import router as marriage_router
    from app.features.mmr.handlers import router as mmr_router
    from app.features.payments.handlers import router as payments_router


    from app.features.para.handlers import router as para_router
    from app.features.pidor.handlers import router as pidor_router
    from app.features.profile.handlers import router as profile_router
    from app.features.ratings.handlers import router as ratings_router
    from app.features.reputation.handlers import router as reputation_router
    from app.features.social.handlers import router as social_router


    from app.features.treasure.handlers import router as treasure_router

    from app.features.welcome.handlers import router as welcome_router

    return [
        # Привязка сайта (/start link_<token>) и приём подарка по ссылке
        # (/start gift_<token>) — ДО help_router, который перехватывает любой
        # /start своим общим фильтром.
        linking_router,
        gift_claim_router,
        welcome_router,

        # Репутация — РАНЬШЕ командных роутеров: reply-фраза («топ», «+», ...)
        # должна срабатывать как репутация. Фильтр требует reply + точный
        # алиас, поэтому обычные команды (не-reply «топ» и т.п.) спокойно
        # проходят дальше к своим роутерам.
        reputation_router,
        # Платежи Stars: pre_checkout/successful_payment должны ловиться рано.
        payments_router,
        farm_router,

        casino_router,
        cases_router,
        gifts_router,
        duel_router,


        treasure_router,
        pidor_router,
        para_router,
        marriage_router,
        social_router,

        profile_router,
        balance_router,
        ratings_router,
        achievements_router,
        inventory_router,
        mmr_router,
        help_router,


        admin_router,
    ]
