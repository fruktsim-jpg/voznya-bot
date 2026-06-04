"""Настройка логирования приложения."""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """Настраивает корневой логгер с читаемым форматом."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # Снижаем шум от сторонних библиотек
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Возвращает именованный логгер."""
    return logging.getLogger(name)
