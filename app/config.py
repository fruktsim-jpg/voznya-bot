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
    website_url: str = Field(default="https://voznya.vercel.app")

    # Время
    timezone: str = "Europe/Amsterdam"
    nomination_reset_hour: int = 12

    # Логирование
    log_level: str = "INFO"

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
