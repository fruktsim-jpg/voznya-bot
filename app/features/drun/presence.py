"""DrunPresence — surface-agnostic output layer for the Dark Drun.

Phase 3 of the "central intelligence" track. Historically Drun could only speak
in ONE place: ``bot.send_message(settings.chat_id, ...)`` hardcoded across
``autonomous``/``events_listener``/``reply_handlers``. The intelligence (memory,
worldview, opinions) is already unified in the shared DB, but the VOICE was
bolted to the Telegram group.

This module separates "what Drun says" from "WHERE it lands". One memory, one
worldview, many surfaces:

* ``GROUP``    — the main Telegram group (the classic surface);
* ``DM``       — a private Telegram chat (owner management, Phase 2);
* ``WEB``      — a read-only "Drun says" feed the site can render (persisted to
                 ``ai_messages`` with ``channel='web'`` — no new table, the site
                 already reads ``ai_messages`` in the admin AI history viewer).

Design principles (consistent with the rest of drun):
* Never breaks gameplay: a failed delivery is logged and swallowed, the caller's
  transaction/outcome already stands.
* No new authority: Presence only DELIVERS text that the existing pipeline already
  decided to produce. It does not generate, moderate, or move money.
* Telegram-side sends happen OUTSIDE the DB transaction (aiogram calls must not
  run inside a session); web persistence happens on a provided session.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger

logger = get_logger(__name__)


class Surface(str, Enum):
    """Where a Drun utterance is delivered."""

    GROUP = "group"      # main Telegram group chat
    DM = "dm"            # private Telegram chat (e.g. the owner)
    WEB = "web"          # site/mini-app "Drun says" feed (persisted, not pushed)


@dataclass(frozen=True)
class PresenceTarget:
    """A concrete destination for one utterance.

    * GROUP — uses the configured group ``chat_id`` (``chat_id`` optional override);
    * DM    — ``chat_id`` is the recipient's Telegram user id (private chat);
    * WEB   — ``user_id`` optionally scopes the feed entry to a player.
    """

    surface: Surface
    chat_id: int | None = None      # telegram chat/user id for GROUP/DM
    user_id: int | None = None      # logical player id (web feed / dossier link)
    reply_to_message_id: int | None = None

    @classmethod
    def group(cls, chat_id: int) -> "PresenceTarget":
        return cls(surface=Surface.GROUP, chat_id=chat_id)

    @classmethod
    def dm(cls, user_id: int) -> "PresenceTarget":
        # In Telegram a private chat id equals the user's id.
        return cls(surface=Surface.DM, chat_id=user_id, user_id=user_id)

    @classmethod
    def web(cls, user_id: int | None = None) -> "PresenceTarget":
        return cls(surface=Surface.WEB, user_id=user_id)


@dataclass
class DeliveryResult:
    """Outcome of one delivery attempt."""

    ok: bool
    surface: Surface
    error: str = ""
    message_id: int | None = None


class DrunPresence:
    """Single object that knows how to make Drun speak on any surface.

    Holds the shared ``Bot`` and group ``chat_id`` so call sites stop hardcoding
    ``bot.send_message(chat_id, ...)``. Wired once in ``main.py`` and reused by
    autonomous posting, the events listener, owner DM, and (later) the web feed.
    """

    def __init__(
        self,
        *,
        bot: Bot,
        group_chat_id: int,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._bot = bot
        self._group_chat_id = group_chat_id
        self._sessionmaker = sessionmaker

    @property
    def group_chat_id(self) -> int:
        return self._group_chat_id

    async def deliver(
        self,
        target: PresenceTarget,
        text: str,
        *,
        parse_mode: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        """Delivers ``text`` to ``target``. Never raises — logs and degrades."""
        if not text or not text.strip():
            return DeliveryResult(ok=False, surface=target.surface, error="empty")

        if target.surface in (Surface.GROUP, Surface.DM):
            chat_id = target.chat_id if target.chat_id is not None else self._group_chat_id
            return await self._deliver_telegram(
                chat_id, text,
                parse_mode=parse_mode,
                reply_to_message_id=target.reply_to_message_id,
                surface=target.surface,
            )
        if target.surface == Surface.WEB:
            return await self._deliver_web(text, user_id=target.user_id, meta=meta)
        return DeliveryResult(ok=False, surface=target.surface, error="unknown_surface")

    async def say_group(self, text: str, *, parse_mode: str | None = None) -> DeliveryResult:
        """Shortcut: post to the main group."""
        return await self.deliver(
            PresenceTarget.group(self._group_chat_id), text, parse_mode=parse_mode
        )

    async def say_dm(self, user_id: int, text: str, *, parse_mode: str | None = None) -> DeliveryResult:
        """Shortcut: send a private message (owner management, Phase 2)."""
        return await self.deliver(PresenceTarget.dm(user_id), text, parse_mode=parse_mode)

    async def _deliver_telegram(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None,
        reply_to_message_id: int | None,
        surface: Surface,
    ) -> DeliveryResult:
        try:
            msg = await self._bot.send_message(
                chat_id,
                text,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to_message_id,
            )
            return DeliveryResult(ok=True, surface=surface, message_id=msg.message_id)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            # Forbidden in a DM = user never started the bot; not an error worth
            # crashing on — Drun simply can't reach that surface right now.
            logger.warning(
                "drun presence: telegram send failed (surface=%s chat=%s): %s",
                surface.value, chat_id, exc,
            )
            return DeliveryResult(ok=False, surface=surface, error=str(exc))
        except Exception:  # noqa: BLE001
            logger.warning(
                "drun presence: telegram send crashed (surface=%s)", surface.value,
                exc_info=True,
            )
            return DeliveryResult(ok=False, surface=surface, error="send_failed")

    async def _deliver_web(
        self,
        text: str,
        *,
        user_id: int | None,
        meta: dict[str, Any] | None,
    ) -> DeliveryResult:
        """Persists an utterance to the WEB feed (``ai_messages`` channel='web').

        The site reads ``ai_messages`` already (admin AI history). A read-only
        "Drun says" feed on the live page is then just a query for
        ``channel='web' AND role='assistant'`` — no push, no new table.
        """
        if self._sessionmaker is None:
            return DeliveryResult(ok=False, surface=Surface.WEB, error="no_sessionmaker")
        try:
            from app.models import AiMessage

            async with self._sessionmaker() as session:
                session.add(
                    AiMessage(
                        channel="web",
                        role="assistant",
                        content=text.strip(),
                        user_id=user_id,
                        meta={"surface": "web", **(meta or {})},
                    )
                )
                await session.commit()
            return DeliveryResult(ok=True, surface=Surface.WEB)
        except Exception:  # noqa: BLE001
            logger.warning("drun presence: web persist failed", exc_info=True)
            return DeliveryResult(ok=False, surface=Surface.WEB, error="persist_failed")


# --- Process-wide singleton (wired in main.py, used everywhere) --------------

_presence: DrunPresence | None = None


def setup_presence(
    *,
    bot: Bot,
    group_chat_id: int,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> DrunPresence:
    """Creates and registers the process-wide presence (called from main.py)."""
    global _presence
    _presence = DrunPresence(
        bot=bot, group_chat_id=group_chat_id, sessionmaker=sessionmaker
    )
    return _presence


def get_presence() -> DrunPresence | None:
    """Returns the registered presence, or None if not wired (e.g. tests)."""
    return _presence
