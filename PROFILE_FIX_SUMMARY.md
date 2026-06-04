# Исправление команды /профиль

## Проблема
Команда `/профиль` не отображала:
1. **Достижения** - количество открытых достижений
2. **Брак** - информацию о партнере, если пользователь в браке

## Решение

### Изменённые файлы
- `app/features/profile/handlers.py`

### Внесённые изменения

#### 1. Добавлены импорты
```python
from app.features.achievements.service import get_unlocked_codes
from app.repositories.marriages import get_active_marriage
from app.repositories.users import get_user
from app.settings.achievements import ACHIEVEMENTS
```

#### 2. Добавлено отображение достижений
```python
# Достижения
unlocked = await get_unlocked_codes(session, user.user_id)
total_achievements = len(ACHIEVEMENTS)
opened_achievements = len(unlocked)
text += f"🏅 Достижения: {opened_achievements}/{total_achievements}\n"
```

#### 3. Добавлено отображение брака
```python
# Брак
marriage = await get_active_marriage(session, user.user_id)
if marriage:
    partner_id = marriage.user_id_2 if marriage.user_id_1 == user.user_id else marriage.user_id_1
    partner = await get_user(session, partner_id)
    if partner:
        partner_name = partner.display_name()
        text += f"💍 В браке с {partner_name}\n"
```

## Проверка отображения

Теперь профиль показывает:
- ✅ **Титул** - эмодзи и название титула на основе total_earned
- ✅ **Баланс** - текущий баланс пользователя в ешках
- ✅ **Достижения** - количество открытых/всего достижений (формат: X/Y)
- ✅ **Брак** - имя партнера, если пользователь в активном браке
- ✅ **Статистика** - дуэли (победы/поражения), серия фермы, клады

## Статистика изменений

```
app/features/profile/handlers.py | 20 ++++++++++++++++++++
1 file changed, 20 insertions(+)
```

## Следующие шаги

Изменения готовы к коммиту. Для применения выполните:

```bash
git add app/features/profile/handlers.py
git commit -m "fix: добавлено отображение достижений и брака в профиле"
git push
```
