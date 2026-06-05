"""Запросы привязки OIDC-аккаунтов к игрокам (``account_links``).

Используется хендлером привязки (см. :mod:`app.features.linking.handlers`),
который вызывается при открытии deep-link ``t.me/<bot>?start=link_<token>``.

Инварианты (защищены ограничениями БД И этим кодом):

* связь ``oidc_sub ↔ user_id`` взаимно-однозначная (биекция);
* токен одноразовый: гасится атомарно (``DELETE ... RETURNING``), повторное
  использование невозможно даже при гонке двух параллельных открытий;
* протухший токен не создаёт связь, но всё равно удаляется (replay исключён);
* нельзя «угнать» уже привязанный аккаунт: если ``sub`` или ``user_id`` уже
  связаны с другой стороной, возвращается :attr:`LinkResult.CONFLICT`, а не
  тихое перепривязывание.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import AccountLink, OidcLinkRequest


class LinkResult(Enum):
    """Итог попытки привязки по одноразовому токену."""

    LINKED = "linked"          # связь создана (или подтверждена та же пара)
    NOT_FOUND = "not_found"    # токена нет (опечатка/уже использован)
    EXPIRED = "expired"        # токен найден, но просрочен
    CONFLICT = "conflict"      # sub или user_id уже привязаны к другой стороне


@dataclass(slots=True)
class LinkOutcome:
    result: LinkResult
    oidc_sub: str | None = None


async def consume_link_request(
    session: AsyncSession, token: str, user_id: int
) -> LinkOutcome:
    """Гасит одноразовый токен и создаёт связь ``oidc_sub -> user_id``.

    Всё выполняется в рамках одной сессии/транзакции (коммит — на стороне
    middleware ``DbSessionMiddleware``).

    Алгоритм:

    1. Атомарно удаляем строку токена через ``DELETE ... RETURNING``. Это
       одновременно гасит токен (одноразовость) и отдаёт ``oidc_sub`` +
       ``expires_at``. При гонке двух параллельных запросов строку получит
       РОВНО один: PostgreSQL берёт блокировку строки на удаление.
    2. Если строки не было — :attr:`LinkResult.NOT_FOUND`.
    3. Если токен просрочен — :attr:`LinkResult.EXPIRED` (токен уже сожжён).
    4. Проверяем биекцию и создаём связь:
       * та же пара уже есть → идемпотентный :attr:`LinkResult.LINKED`;
       * ``sub`` свободен и ``user_id`` свободен → новая связь, ``LINKED``;
       * иначе (любая из сторон занята кем-то другим) →
         :attr:`LinkResult.CONFLICT` без перепривязки.

    :returns: :class:`LinkOutcome` с результатом и (если найден) ``oidc_sub``.
    """
    # (1) Атомарно гасим токен и забираем его данные. RETURNING гарантирует,
    # что при конкурентном использовании строку получит только один вызов.
    burned = (
        await session.execute(
            delete(OidcLinkRequest)
            .where(OidcLinkRequest.token == token)
            .returning(OidcLinkRequest.oidc_sub, OidcLinkRequest.expires_at)
        )
    ).first()

    if burned is None:
        return LinkOutcome(LinkResult.NOT_FOUND)

    oidc_sub, expires_at = burned

    # (3) Просроченный токен сожжён, но связь не создаём.
    if expires_at <= now_utc():
        return LinkOutcome(LinkResult.EXPIRED, oidc_sub=oidc_sub)

    # (4) Проверяем обе стороны биекции до вставки.
    sub_owner = await session.scalar(
        select(AccountLink.user_id).where(AccountLink.oidc_sub == oidc_sub)
    )
    user_link = await session.scalar(
        select(AccountLink.oidc_sub).where(AccountLink.user_id == user_id)
    )

    # Та же пара уже подтверждена — идемпотентный успех (юзер нажал Start дважды).
    if sub_owner == user_id and user_link == oidc_sub:
        return LinkOutcome(LinkResult.LINKED, oidc_sub=oidc_sub)

    # Любая из сторон уже занята другой — не перепривязываем (защита от угона).
    if sub_owner is not None or user_link is not None:
        return LinkOutcome(LinkResult.CONFLICT, oidc_sub=oidc_sub)

    # Обе стороны свободны — создаём связь. На случай гонки с другим токеном,
    # который успел вставить пересекающуюся связь между нашими SELECT и INSERT,
    # ловим нарушение уникальности в savepoint и отдаём CONFLICT.
    stmt = pg_insert(AccountLink).values(oidc_sub=oidc_sub, user_id=user_id)
    stmt = stmt.on_conflict_do_nothing(index_elements=[AccountLink.oidc_sub])
    try:
        async with session.begin_nested():
            result = await session.execute(stmt)
    except IntegrityError:
        # Гонка по уникальному user_id (uq_account_links_user_id).
        return LinkOutcome(LinkResult.CONFLICT, oidc_sub=oidc_sub)

    # ON CONFLICT DO NOTHING по oidc_sub: rowcount 0 ⇒ sub заняли в гонке.
    if result.rowcount == 0:
        return LinkOutcome(LinkResult.CONFLICT, oidc_sub=oidc_sub)

    return LinkOutcome(LinkResult.LINKED, oidc_sub=oidc_sub)


async def get_user_id_by_sub(session: AsyncSession, oidc_sub: str) -> int | None:
    """Возвращает привязанный ``user_id`` для OIDC ``sub`` (или None)."""
    return await session.scalar(
        select(AccountLink.user_id).where(AccountLink.oidc_sub == oidc_sub)
    )


async def delete_expired_link_requests(session: AsyncSession) -> int:
    """Удаляет протухшие запросы привязки. Возвращает число удалённых строк.

    Вызывается периодической фоновой задачей (см.
    :mod:`app.services.link_maintenance`). Опирается на индекс
    ``ix_oidc_link_requests_expires_at`` для дешёвого диапазонного удаления.
    Протухшие токены и так не создают связь (проверка в
    :func:`consume_link_request`), но чистка не даёт таблице расти бесконечно.
    """
    result = await session.execute(
        delete(OidcLinkRequest).where(OidcLinkRequest.expires_at <= now_utc())
    )
    return result.rowcount or 0
