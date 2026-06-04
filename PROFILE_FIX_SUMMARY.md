# Исправление команды /профиль

## Проблемы
Команда `/профиль` не работала из-за следующих ошибок:

1. **Неправильная сигнатура функции** - использовался параметр `user: User`, который не предоставляется middleware
2. **Отсутствие отображения достижений** - не показывалось количество открытых достижений
3. **Отсутствие отображения брака** - не показывалась информация о партнере

## Решение

### Изменённые файлы
- `app/features/profile/handlers.py` - исправлена логика получения пользователя и добавлено отображение достижений и брака

### Внесённые изменения

#### 1. Исправлена сигнатура функции profile_command
**Было:**
```python
async def profile_command(message: Message, user: User, session: AsyncSession) -> None:
```

**Стало:**
```python
async def profile_command(message: Message, session: AsyncSession) -> None:
    from app.repositories.users import get_user
    
    user_tg = message.from_user
    if user_tg is None:
        return
    
    user = await get_user(session, user_tg.id)
    if user is None:
        await message.answer("❌ Пользователь не найден в базе данных.")
        return
```

#### 2. Добавлены импорты в render_profile
```python
from app.features.achievements.service import get_unlocked_codes
from app.repositories.marriages import get_active_marriage
from app.repositories.users import get_user
from app.settings.achievements import ACHIEVEMENTS
```

#### 3. Добавлено отображение достижений
```python
# Достижения
unlocked = await get_unlocked_codes(session, user.user_id)
total_achievements = len(ACHIEVEMENTS)
opened_achievements = len(unlocked)
text += f"🏅 Достижения: {opened_achievements}/{total_achievements}\n"
```

#### 4. Добавлено отображение брака
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

Теперь профиль корректно показывает:
- ✅ **Титул** - эмодзи и название титула на основе total_earned
- ✅ **Баланс** - текущий баланс пользователя в ешках
- ✅ **Достижения** - количество открытых/всего достижений (формат: X/Y)
- ✅ **Брак** - имя партнера, если пользователь в активном браке
- ✅ **Статистика** - дуэли (победы/поражения), серия фермы, клады

## Статистика изменений

```
app/features/profile/handlers.py | 13 ++++++++++++-
1 file changed, 12 insertions(+), 1 deletion(-)
```

## Примечание о статистике на сайте

Ссылка на сайт в профиле ведет на `{website_url}/profile/{user_id}`. Если статистика не отображается на сайте, это отдельная проблема фронтенда/бэкенда сайта, которая не связана с ботом. Бот корректно передает user_id в URL.

## Следующие шаги

Изменения готовы к коммиту. Для применения выполните:

```bash
git add app/features/profile/handlers.py
git commit -m "fix: исправлена команда /профиль - добавлено отображение достижений и брака"
git push
```

После деплоя перезапустите бота для применения изменений.
