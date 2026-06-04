"""Тестовый скрипт для проверки импорта модуля профиля."""
import sys
import traceback

try:
    # Попытка импортировать модуль
    from app.features.profile import handlers
    print("✅ Модуль app.features.profile.handlers успешно импортирован")
    print(f"✅ Router: {handlers.router}")
    print(f"✅ Функция render_profile: {handlers.render_profile}")
    print(f"✅ Функция profile_command: {handlers.profile_command}")
except Exception as e:
    print("❌ ОШИБКА ПРИ ИМПОРТЕ:")
    print(f"Тип ошибки: {type(e).__name__}")
    print(f"Сообщение: {str(e)}")
    print("\nПолный traceback:")
    traceback.print_exc()
    sys.exit(1)

print("\n✅ Все проверки пройдены успешно!")
