"""Дроп-лист кейса: возможные награды и их целочисленные веса.

Одна строка = один возможный дроп. Вероятность выпадения = ``weight`` / сумма
весов активных строк кейса. Награда — либо предмет каталога (``reward_kind='item'``,
``reward_item_code`` → inventory_items.code), либо ешки (``reward_kind='currency'``,
``amount``).

``reward_kind`` допускает на уровне СХЕМЫ значения ``tg_gift`` и ``stars`` —
это задел под будущее. Код V1 их отклоняет валидатором (см. features/cases), так
что их добавление позже не потребует миграции существующих кейсов.

Лимитированные награды (джекпоты): ``max_global_supply`` + ``granted_count`` —
выпадение блокируется, когда лимит исчерпан.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Вид награды. V1-код принимает только item и currency; tg_gift/stars — задел,
# разрешённый схемой (CheckConstraint), но запрещённый валидатором V1.
REWARD_KINDS_V1 = ("item", "currency")
REWARD_KINDS_ALL = ("item", "currency", "tg_gift", "stars")


class CaseReward(Base):
    """Один возможный дроп кейса (одна строка = одна награда + её вес)."""

    __tablename__ = "case_rewards"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    # → case_definitions.item_code (кейс, которому принадлежит дроп).
    case_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # Один из REWARD_KINDS_ALL (V1-код принимает только REWARD_KINDS_V1).
    reward_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # → inventory_items.code, если reward_kind='item'.
    reward_item_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Ешки, если reward_kind='currency'.
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Целочисленный вес; вероятность = weight / SUM(weight).
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    min_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Лимит выпадений (джекпот); NULL = без лимита.
    max_global_supply: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    granted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    is_jackpot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("weight > 0", name="ck_case_rewards_weight_pos"),
        CheckConstraint(
            "min_qty >= 1 AND max_qty >= min_qty", name="ck_case_rewards_qty"
        ),
        CheckConstraint(
            "granted_count >= 0", name="ck_case_rewards_granted_nonneg"
        ),
        CheckConstraint(
            "max_global_supply IS NULL OR granted_count <= max_global_supply",
            name="ck_case_rewards_supply",
        ),
        CheckConstraint(
            "(reward_kind = 'item' AND reward_item_code IS NOT NULL) "
            "OR (reward_kind = 'currency' AND amount IS NOT NULL AND amount > 0) "
            "OR (reward_kind IN ('tg_gift', 'stars'))",
            name="ck_case_rewards_kind_payload",
        ),
        Index("ix_case_rewards_case", "case_item_code"),
    )
