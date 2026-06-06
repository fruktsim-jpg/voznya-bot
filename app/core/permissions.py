"""Права ролей админ-платформы — единый источник правды.

Здесь описано, что разрешено каждой роли. И бот (админ-команды), и будущая
админ-панель сайта/Mini App должны проверять доступ через этот модуль, чтобы
правила не расходились между Python и TypeScript.

Модель прав — простой набор строковых разрешений (`Permission`), сгруппированных
по ролям. Иерархия ролей задаёт наследование: более высокая роль включает все
права нижестоящих плюс свои. Это покрывает текущие нужды без сложного RBAC с
БД-таблицей разрешений; если правил станет много, набор легко вынести в БД.

Роли (по убыванию прав): owner > admin > moderator > support.

"""

from __future__ import annotations

from app.models.admin_role import ADMIN_ROLES

# --- Каталог разрешений ------------------------------------------------------
# Формат "<домен>.<действие>". Держим плоским и явным.
PERM_VIEW_DASHBOARD = "dashboard.view"
PERM_VIEW_PLAYERS = "players.view"
PERM_EDIT_PLAYERS = "players.edit"          # изменить профиль/заметки
PERM_ECONOMY_VIEW = "economy.view"
PERM_ECONOMY_ADD = "economy.add"            # начислить ешки
PERM_ECONOMY_REMOVE = "economy.remove"      # списать ешки
PERM_INVENTORY_VIEW = "inventory.view"
PERM_INVENTORY_GRANT = "inventory.grant"    # выдать предмет
PERM_INVENTORY_REVOKE = "inventory.revoke"  # удалить предмет
PERM_SHOP_VIEW = "shop.view"
PERM_SHOP_MANAGE = "shop.manage"            # CRUD товаров (будущий магазин)
PERM_LOGS_VIEW = "logs.view"
PERM_MODERATION_VIEW = "moderation.view"
PERM_MODERATION_BAN = "moderation.ban"      # бан/разбан
PERM_ROLES_MANAGE = "roles.manage"          # назначать/менять роли
PERM_GIFT_VIEW = "gift.view"                # просмотр истории подарков
PERM_GIFT_MANAGE = "gift.manage"            # системные/админские подарки
PERM_MMR_VIEW = "mmr.view"                  # просмотр рейтинга/истории MMR
PERM_MMR_ADD = "mmr.add"                    # начислить MMR (награда/ивент)
PERM_MMR_REMOVE = "mmr.remove"              # списать MMR (коррекция)
PERM_REPUTATION_VIEW = "reputation.view"    # просмотр репутации/истории
PERM_REPUTATION_ADD = "reputation.add"      # выдать репутацию
PERM_REPUTATION_REMOVE = "reputation.remove"  # снять репутацию
PERM_ACHIEVEMENTS_VIEW = "achievements.view"      # просмотр достижений
PERM_ACHIEVEMENTS_GRANT = "achievements.grant"    # выдать достижение
PERM_ACHIEVEMENTS_REVOKE = "achievements.revoke"  # отозвать достижение
PERM_CASES_VIEW = "cases.view"      # просмотр кейсов, дроп-листов, истории
PERM_CASES_MANAGE = "cases.manage"  # CRUD кейсов и дроп-листов
PERM_CASES_GRANT = "cases.grant"    # выдать кейс игроку (item-выдача)




# --- Права по ролям ----------------------------------------------------------
# Для каждой роли — её СОБСТВЕННЫЕ права. Полный набор роли вычисляется с учётом
# наследования (см. role_permissions).
_SUPPORT: frozenset[str] = frozenset(
    {
        PERM_VIEW_DASHBOARD,
        PERM_VIEW_PLAYERS,
        PERM_ECONOMY_VIEW,
        PERM_INVENTORY_VIEW,
        PERM_SHOP_VIEW,
        PERM_MODERATION_VIEW,
        PERM_GIFT_VIEW,
        PERM_MMR_VIEW,
        PERM_REPUTATION_VIEW,
        PERM_ACHIEVEMENTS_VIEW,
        PERM_CASES_VIEW,
    }
)




# moderator: support + модерация и просмотр логов.
_MODERATOR: frozenset[str] = _SUPPORT | frozenset(
    {
        PERM_MODERATION_BAN,
        PERM_LOGS_VIEW,
        PERM_EDIT_PLAYERS,
    }
)

# admin: moderator + экономика и инвентарь и управление магазином.
_ADMIN: frozenset[str] = _MODERATOR | frozenset(
    {
        PERM_ECONOMY_ADD,
        PERM_ECONOMY_REMOVE,
        PERM_INVENTORY_GRANT,
        PERM_INVENTORY_REVOKE,
        PERM_SHOP_MANAGE,
        PERM_GIFT_MANAGE,
        PERM_MMR_ADD,
        PERM_MMR_REMOVE,
        PERM_REPUTATION_ADD,
        PERM_REPUTATION_REMOVE,
        PERM_ACHIEVEMENTS_GRANT,
        PERM_ACHIEVEMENTS_REVOKE,
        PERM_CASES_MANAGE,
        PERM_CASES_GRANT,
    }
)




# owner: всё, включая управление ролями.
_OWNER: frozenset[str] = _ADMIN | frozenset({PERM_ROLES_MANAGE})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "support": _SUPPORT,
    "moderator": _MODERATOR,
    "admin": _ADMIN,
    "owner": _OWNER,
}

# Численный ранг для сравнения «кто старше» (нельзя менять роль выше своей).
ROLE_RANK: dict[str, int] = {
    "support": 1,
    "moderator": 2,
    "admin": 3,
    "owner": 4,
}


def role_permissions(role: str | None) -> frozenset[str]:
    """Возвращает полный набор прав роли (с наследованием). None → пусто."""
    if not role:
        return frozenset()
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: str | None, permission: str) -> bool:
    """Проверяет, есть ли у роли указанное разрешение."""
    return permission in role_permissions(role)


def can_manage_role(actor_role: str | None, target_role: str) -> bool:
    """Можно ли актору назначать/менять роль ``target_role``.

    Управлять ролями вправе только owner, и нельзя назначить роль выше или
    равную собственному рангу (owner не может «клонировать» owner'ов случайно —
    это сознательное ограничение; добавление owner делается отдельным
    bootstrap-путём из ADMIN_IDS).
    """
    if not has_permission(actor_role, PERM_ROLES_MANAGE):
        return False
    if target_role not in ADMIN_ROLES:
        return False
    actor_rank = ROLE_RANK.get(actor_role or "", 0)
    target_rank = ROLE_RANK.get(target_role, 0)
    return target_rank < actor_rank
