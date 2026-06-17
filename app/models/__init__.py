"""Модели базы данных.

Импортируем все модели здесь, чтобы они регистрировались в общем
``Base.metadata`` (нужно для Alembic и создания схемы).
"""

from app.models.account_link import AccountLink
from app.models.admin_role import AdminRole
from app.models.ai import AiMemory, AiMessage, AiPrompt, AiSetting
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog

from app.models.base import Base
from app.models.case_definition import CaseDefinition
from app.models.case_opening import CaseOpening
from app.models.case_reward import CaseReward
from app.models.combot_activity_heatmap import CombotActivityHeatmap

from app.models.combot_daily_stats import CombotDailyStats
from app.models.combot_import_run import CombotImportRun
from app.models.combot_user_stats import CombotUserStats
from app.models.cooldown import Cooldown
from app.models.gift_catalog import GiftCatalog
from app.models.gift_transaction import GiftTransaction

from app.models.inventory import Inventory

from app.models.inventory_history import InventoryHistory
from app.models.inventory_instance import InventoryInstance
from app.models.inventory_item import InventoryItem
from app.models.marriage import Marriage


from app.models.message_daily import MessageDaily
from app.models.mmr_entry import MmrEntry
from app.models.mod_warning import ModWarning
from app.models.user_moderation import UserModeration

from app.models.nomination import DailyNomination
from app.models.oidc_link_request import OidcLinkRequest
from app.models.pending_action import PendingAction
from app.models.purchase_history import PurchaseHistory
from app.models.reputation_entry import ReputationEntry
from app.models.scheduled_deletion import ScheduledDeletion
from app.models.season import Season
from app.models.season_progress import (
    DailyClaim,
    LoginStreak,
    SeasonMmrEntry,
    SeasonTitleAward,
    WeeklyMissionProgress,
)


from app.models.shop_category import ShopCategory
from app.models.shop_offer import ShopOffer
from app.models.stars_ledger import StarsLedger

from app.models.transaction import Transaction

from app.models.treasure import Treasure
from app.models.user import User
from app.models.user_achievement import UserAchievement
from app.models.world_event import WorldEvent

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
    "ModWarning",
    "UserModeration",
    "AdminRole",
    "AppSetting",
    "AuditLog",
    "InventoryItem",
    "Inventory",
    "InventoryHistory",
    "InventoryInstance",
    "CaseDefinition",
    "CaseReward",
    "CaseOpening",
    "ShopCategory",

    "ShopOffer",
    "PurchaseHistory",
    "GiftCatalog",
    "GiftTransaction",
    "StarsLedger",


    "ReputationEntry",
    "MmrEntry",

    "Season",
    "SeasonMmrEntry",
    "SeasonTitleAward",
    "LoginStreak",
    "DailyClaim",
    "WeeklyMissionProgress",


    "CombotUserStats",

    "CombotDailyStats",
    "CombotActivityHeatmap",
    "CombotImportRun",
    "WorldEvent",
    "AiSetting",
    "AiPrompt",
    "AiMessage",
    "AiMemory",
]





