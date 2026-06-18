"""Управление сервером: друн как руки админки (вкл/выкл фич, тюнинг, оповещения).

Владелец говорит «друн выруби казино», «подними максимальную ставку до 5000»,
«сделай ивент х2 на ешки» — и друн реально дёргает ``app_settings`` (те же
ключи, что и веб-админка), сбрасывает кэш и рапортует.

Безопасность: пишем ТОЛЬКО по белому списку ключей (``_WRITABLE``) с проверкой
типа и клампом диапазона. Любой неизвестный ключ отвергаем — нельзя через LLM
записать произвольную настройку. Всё логируется в audit_log.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
from app.settings import dynamic as dyn


@dataclass(frozen=True)
class SettingSpec:
    """Описание одного управляемого ключа: тип, диапазон, категория, ярлык."""

    key: str
    kind: str            # "bool" | "int" | "float"
    category: str
    label: str           # человекочитаемое имя для отчёта
    lo: float = 0.0      # для int/float — нижняя граница
    hi: float = 0.0      # для int/float — верхняя граница


# Белый список того, что друну позволено крутить. Диапазоны — предохранители,
# чтобы случайной/кривой командой не сломать экономику.
_WRITABLE: dict[str, SettingSpec] = {
    s.key: s for s in (
        # Фич-флаги (вкл/выкл целых подсистем).
        SettingSpec("casino.enabled", "bool", "casino", "казино"),
        SettingSpec("duel.enabled", "bool", "duel", "бои"),
        SettingSpec("farm.enabled", "bool", "farm", "ферма"),
        SettingSpec("cases.enabled", "bool", "cases", "кейсы"),
        SettingSpec("shop.enabled", "bool", "shop", "магазин"),
        SettingSpec("gifts.enabled", "bool", "gifts", "подарки"),
        # Тюнинг ставок/кулдаунов.
        SettingSpec("casino.min_bet", "int", "casino", "мин. ставка казино", 1, 1_000_000),
        SettingSpec("casino.max_bet", "int", "casino", "макс. ставка казино", 1, 100_000_000),
        SettingSpec("casino.cooldown", "int", "casino", "кд казино (сек)", 0, 86_400),
        SettingSpec("duel.min_bet", "int", "duel", "мин. ставка боя", 1, 1_000_000),
        SettingSpec("duel.max_bet", "int", "duel", "макс. ставка боя", 1, 100_000_000),
        SettingSpec("duel.cooldown", "int", "duel", "кд боёв (сек)", 0, 86_400),
        SettingSpec("farm.cooldown", "int", "farm", "кд фермы (сек)", 0, 604_800),
        SettingSpec("farm.bonus", "float", "farm", "бонус фермы", 0.0, 10.0),
        # Множители-ивенты.
        SettingSpec("modifier.eshki", "float", "economy", "множитель ешек", 0.1, 5.0),
        SettingSpec("modifier.drop", "float", "cases", "множитель дропа", 0.1, 5.0),
        SettingSpec("modifier.xp", "float", "mmr", "множитель MMR/опыта", 0.1, 5.0),
    )
}


def is_writable(key: str) -> bool:
    """Разрешён ли ключ к записи друном."""
    return key in _WRITABLE


def _coerce(spec: SettingSpec, raw) -> object | None:
    """Приводит сырое значение к типу ключа и клампит в диапазон. None — мусор."""
    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on", "вкл", "да"}
        return None
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return None
    num = max(spec.lo, min(spec.hi, num))
    return int(num) if spec.kind == "int" else num


async def set_setting(
    session: AsyncSession, *, key: str, value, owner_id: int,
) -> tuple[bool, str, object | None]:
    """Пишет настройку из белого списка. Возвращает (ok, человекочитаемо, value).

    Сбрасывает кэш dynamic, чтобы изменение подхватилось сразу. Аудит — на
    стороне вызывающего тула (есть _audit), тут только запись значения.
    """
    spec = _WRITABLE.get(key)
    if spec is None:
        return False, f"ключ «{key}» не управляется друном", None
    coerced = _coerce(spec, value)
    if coerced is None:
        return False, f"кривое значение для «{spec.label}»", None
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=coerced, category=spec.category))
    else:
        row.value = coerced
    try:
        dyn.invalidate_cache()
    except Exception:  # noqa: BLE001
        pass
    if spec.kind == "bool":
        human = f"{spec.label}: {'включил' if coerced else 'выключил'}"
    else:
        human = f"{spec.label}: теперь {coerced:g}" if spec.kind == "float" \
            else f"{spec.label}: теперь {coerced}"
    return True, human, coerced
