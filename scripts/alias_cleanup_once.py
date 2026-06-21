"""Разовая чистка мис-привязанных прозвищ + штамповка ts для TTL.

Запускается:
    docker compose exec bot python -m scripts.alias_cleanup_once

Что делает (только AiProfile.data["aliases"], НИКАКОЙ экономики):
1. Выкидывает СЛАБЫЕ (вес ≤1) прозвища, совпадающие с именем ДРУГОГО игрока
   (классическая ошибка LLM: «соня»/«маша»/«эдик» приклеены не к тому).
2. Прогоняет prune_expired: штампует старые алиасы без ts «сейчас» и роняет
   уже протухшие по TTL (вес-зависимый: 14/30/90 дней).

Идемпотентно и безопасно: только чтение имён + перезапись поля aliases в
профилях. Печатает, у скольких профилей что изменилось.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_engine
from app.features.drun import aliases as al
from app.models import AiProfile, User


def _norm_name(first_name: str | None) -> str:
    return al._norm(first_name or "")


async def main() -> None:
    engine = get_engine()
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with sm() as session:
        # Карта user_id → нормализованное имя (для определения «чужого имени»).
        users = (
            await session.execute(
                select(User.user_id, User.first_name).where(
                    User.first_name.is_not(None)
                )
            )
        ).all()
        name_by_uid: dict[int, str] = {}
        for uid, fn in users:
            nm = _norm_name(fn)
            if nm:
                name_by_uid[uid] = nm

        profiles = (
            await session.execute(
                select(AiProfile).where(
                    AiProfile.data.has_key("aliases")  # type: ignore[attr-defined]
                )
            )
        ).scalars().all()

        changed = 0
        dropped_collisions = 0
        dropped_expired = 0
        for prof in profiles:
            data = dict(prof.data or {})
            before = list(data.get("aliases") or [])
            if not before:
                continue
            # Чужие имена = имена всех ДРУГИХ игроков (не самого субъекта).
            foreign = {
                nm for uid, nm in name_by_uid.items() if uid != prof.user_id
            }
            step1 = al.drop_colliding_weak(before, foreign)
            dropped_collisions += len(before) - len(step1)
            step2 = al.prune_expired(step1)
            dropped_expired += len(step1) - len(step2)
            if step2 != before:
                data["aliases"] = step2
                prof.data = data
                changed += 1

        await session.commit()
        print(
            f"profiles scanned: {len(profiles)}, changed: {changed}, "
            f"collision-drops: {dropped_collisions}, expired-drops: {dropped_expired}"
        )


if __name__ == "__main__":
    asyncio.run(main())
