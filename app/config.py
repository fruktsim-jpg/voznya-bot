"""Загрузка и валидация настроек окружения.

Все настройки читаются из переменных окружения (или файла ``.env``).
Это единственное место, где приложение обращается к окружению напрямую.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Глобальные настройки приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Telegram
    bot_token: str
    chat_id: int
    # Список ID администраторов хранится строкой «1,2,3» (разбирается ниже),
    # чтобы pydantic не пытался интерпретировать значение как JSON.
    admin_ids_raw: str = Field(default="", validation_alias="ADMIN_IDS")

    # База данных
    database_url: str

    # Сайт
    website_url: str = Field(default="https://voznya.nl")

    # Глобальные уведомления о редких дропах (Release 2.2). Когда кто-то выбивает
    # действительно редкое — это событие сообщества: бот шлёт сообщение в общий
    # чат (chat_id). Чтобы не спамить, срабатывает ТОЛЬКО при выполнении хотя бы
    # одного из условий ниже. Любой порог можно отключить, выставив его в 0/false.
    rare_drop_announce_enabled: bool = True
    # Анонсировать любой джекпот (is_jackpot).
    rare_drop_announce_jackpot: bool = True
    # Анонсировать любой реальный Telegram Gift / Premium (reward_kind=tg_gift).
    rare_drop_announce_gift: bool = True
    # Анонсировать, если шанс выпадения ниже порога (в процентах). 0 = выключено.
    rare_drop_chance_pct: float = 1.0
    # Анонсировать, если стоимость дропа в ешках не ниже порога. 0 = выключено.
    rare_drop_min_value: int = 1000


    # Время
    timezone: str = "Europe/Amsterdam"
    # Час сброса номинаций (Пидор/Пара дня). 0 = смена в 00:00.
    # Сам выбор остаётся «ленивым»: новый победитель определяется при первом
    # вызове команды после полуночи, а не автоматически в 00:00.
    nomination_reset_hour: int = 0


    # Логирование
    log_level: str = "INFO"

    # Gifts-магазин: реальная выдача Telegram Gifts через Bot API. По умолчанию
    # ВЫКЛЮЧЕНА — покупка работает (ешки списываются, доставка остаётся pending),
    # но реальные Stars не тратятся, пока выдача явно не включена и у бота нет
    # подключённого доступа/баланса Stars. См. TELEGRAM_GIFTS_AUDIT.md.
    gifts_delivery_enabled: bool = False

    # Внутренний HTTP-API бота для сайта (открытие кейсов через веб тем же
    # open_case, без дублирования логики). Поднимается рядом с polling.
    # Включается только при заданном секрете; слушает внутренний адрес
    # (в docker-сети), наружу публиковать НЕЛЬЗЯ. См. app/web/internal_api.py.
    internal_api_enabled: bool = False
    internal_api_host: str = "0.0.0.0"
    internal_api_port: int = 8081
    internal_api_secret: str = Field(default="", validation_alias="INTERNAL_API_SECRET")


    @property
    def admin_ids(self) -> list[int]:

        """Список ID администраторов, разобранный из строки ADMIN_IDS."""
        raw = self.admin_ids_raw or ""
        return [int(part.strip()) for part in raw.split(",") if part.strip()]

    def is_admin(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь администратором бота."""
        return user_id in self.admin_ids


@lru_cache
def get_settings() -> Settings:
    """Возвращает закэшированный экземпляр настроек."""
    return Settings()  # type: ignore[call-arg]
