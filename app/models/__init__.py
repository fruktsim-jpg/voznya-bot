"""Модели базы данных.

Импортируем все модели здесь, чтобы они регистрировались в общем
``Base.metadata`` (нужно для Alembic и создания схемы).
"""

from app.models.account_link import AccountLink
from app.models.admin_role import AdminRole
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.cooldown import Cooldown
from app.models.gift_transaction import GiftTransaction
from app.models.inventory import Inventory

from app.models.inventory_history import InventoryHistory
from app.models.inventory_item import InventoryItem
from app.models.marriage import Marriage

from app.models.message_daily import MessageDaily
from app.models.nomination import DailyNomination
from app.models.oidc_link_request import OidcLinkRequest
from app.models.pending_action import PendingAction
from app.models.purchase_history import PurchaseHistory
from app.models.scheduled_deletion import ScheduledDeletion
from app.models.shop_category import ShopCategory
from app.models.shop_offer import ShopOffer
from app.models.transaction import Transaction

from app.models.treasure import Treasure
from app.models.user import User
from app.models.user_achievement import UserAchievement

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
    "UserAchievement",
    "MessageDaily",
    "AccountLink",
    "OidcLinkRequest",
    "AdminRole",
    "AuditLog",
    "InventoryItem",
    "Inventory",
    "InventoryHistory",
    "ShopCategory",
    "ShopOffer",
    "PurchaseHistory",
    "GiftTransaction",
]





