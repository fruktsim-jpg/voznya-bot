"""Запросы привязки OIDC-аккаунтов к игрокам (``account_links``).

Используется хендлером привязки (см. :mod:`app.features.linking.handlers`),
который вызывается при открытии deep-link ``t.me/<bot>?start=link_<token>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import AccountLink, OidcLinkRequest


class LinkResult(Enum):
    """Итог попытки привязки по одноразовому токену."""

    LINKED = "linked"          # связь создана (или обновлена) успешно
    NOT_FOUND = "not_found"    # токена нет (опечатка/уже использован)
    EXPIRED = "expired"        # токен найден, но просрочен


@dataclass(slots=True)
class LinkOutcome:
    result: LinkResult
    oidc_sub: str | None = None


async def consume_link_request(
    session: AsyncSession, token: str, user_id: int
) -> LinkOutcome:
    """Гасит одноразовый токен и создаёт связь ``oidc_sub -> user_id``.

    Всё выполняется в рамках одной сессии/транзакции (коммит — на стороне
    middleware ``DbSessionMiddleware``). Токен удаляется в любом валидном
    исходе (найден), чтобы исключить повторное использование.

    :returns: :class:`LinkOutcome` с результатом и (если найден) ``oidc_sub``.
    """
    request = await session.get(OidcLinkRequest, token)
    if request is None:
        return LinkOutcome(LinkResult.NOT_FOUND)

    oidc_sub = request.oidc_sub

    # Токен найден — удаляем его сразу (одноразовость), независимо от срока.
    await session.execute(
        delete(OidcLinkRequest).where(OidcLinkRequest.token == token)
    )

    if request.expires_at <= now_utc():
        return LinkOutcome(LinkResult.EXPIRED, oidc_sub=oidc_sub)

    # Создаём/обновляем связь. Один Telegram-аккаунт может перепривязать sub
    # к себе (ON CONFLICT по oidc_sub обновит user_id).
    stmt = pg_insert(AccountLink).values(oidc_sub=oidc_sub, user_id=user_id)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AccountLink.oidc_sub],
        set_={"user_id": user_id},
    )
    await session.execute(stmt)

    return LinkOutcome(LinkResult.LINKED, oidc_sub=oidc_sub)


async def get_user_id_by_sub(session: AsyncSession, oidc_sub: str) -> int | None:
    """Возвращает привязанный ``user_id`` для OIDC ``sub`` (или None)."""
    return await session.scalar(
        select(AccountLink.user_id).where(AccountLink.oidc_sub == oidc_sub)
    )
