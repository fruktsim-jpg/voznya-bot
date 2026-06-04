"""Модели базы данных.

Импортируем все модели здесь, чтобы они регистрировались в общем
``Base.metadata`` (нужно для Alembic и создания схемы).
"""

from app.models.base import Base
from app.models.cooldown import Cooldown
from app.models.marriage import Marriage
from app.models.nomination import DailyNomination
from app.models.pending_action import PendingAction
from app.models.scheduled_deletion import ScheduledDeletion
from app.models.transaction import Transaction
from app.models.treasure import Treasure
from app.models.user import User

__all__ = [
    "Base",
    "User",
    "Transaction",
    "Cooldown",
    "DailyNomination",
    "Marriage",
    "PendingAction",
    "Treasure",
    "ScheduledDeletion",
]
