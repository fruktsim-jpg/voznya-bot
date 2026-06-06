# REPOSITORY CLEANUP REPORT — Возня

Дата: 2026-06-06. Цель: привести репозиторий в порядок перед этапом локализации —
оставить только актуальную документацию и рабочий код. Ничего не сломано, логика
не менялась, новые механики не добавлялись.

Что уже выполнено в этом проходе:
- создан `docs/archive/` и туда перемещены устаревшие одноразовые `*.md`
  (через `git mv`, история сохранена);
- переписан `README.md` под реальное состояние проекта;
- обновлён `AGENTS.md` (убрано устаревшее «greenfield stub»);
- обновлены статус-баннеры foundation-доков.

Что требует подтверждения: удаление мусорных файлов (см. §3).

---

## 1. Что оставить (KEEP)

Актуальная документация в корне и `docs/`:

| Документ | Назначение |
|---|---|
| `README.md` | ✅ переписан — реальное состояние, архитектура, запуск |
| `CHANGELOG.md` | история версий (v1.3 эконом-ребаланс) |
| `AGENTS.md` | ✅ обновлён — гайд для агентов |
| `PROJECT_STATE_REPORT.md` | полный аудит состояния проекта |
| `REPOSITORY_CLEANUP_REPORT.md` | этот документ |
| `FOUNDATION_STATUS.md` | состояние auth/моделей/расширений |
| `MMR_FOUNDATION.md` | ✅ баннер «РЕАЛИЗОВАНО» |
| `REPUTATION_FOUNDATION.md` | ✅ баннер «РЕАЛИЗОВАНО» |
| `INVENTORY_FOUNDATION.md` | ✅ баннер «FOUNDATION ONLY» |
| `SHOP_FOUNDATION.md` | ✅ баннер «FOUNDATION ONLY» |
| `GIFT_FOUNDATION.md` | ✅ баннер «FOUNDATION ONLY» |
| `ADMIN_PLATFORM.md` | RBAC + audit (реализовано) |
| `COMBOT_IMPORT_PLAN.md` | план/инструкция импорта Combot |
| `COMBOT_MIGRATION.md` | детали миграции Combot |
| `MINI_APP_PLAN.md` | план Mini App (не реализован, но актуальный план) |
| `DELETION_ARCHITECTURE.md` | описывает `app/services/deletion.py` |
| `docs/ECONOMY.md` | баланс экономики |
| `DEBUG_INSTRUCTIONS.md`, `VPS_DIAGNOSTIC_COMMANDS.md`, `VPS_RESTART_INSTRUCTIONS.md` | операционные шпаргалки (справочное; оставить) |
| `APPROVED_CHANGES.md` | журнал согласований (справочное; оставить) |

---

## 2. Что архивировать (ARCHIVE) — ВЫПОЛНЕНО

Перемещено в `docs/archive/` через `git mv` (история сохранена). Это одноразовые
отладочные отчёты, перекрытые текущим кодом и аудитом:

| Файл | Причина |
|---|---|
| `DUEL_BUG_FIX_REPORT.md` | разовый отчёт о фиксе дуэлей |
| `OPEN_DUEL_FIX.md` | разовый отчёт о фиксе дуэлей |
| `PROFILE_COMMAND_DIAGNOSIS.md` | одноразовая диагностика `/профиль` |
| `PROFILE_COMMAND_FIX.md` | одноразовый отчёт о фиксе |
| `PROFILE_ERROR_ANALYSIS.md` | одноразовый разбор ошибки |
| `PROFILE_FINAL_REPORT.md` | одноразовый отчёт |
| `PROFILE_FIX_APPLIED.md` | одноразовый отчёт |
| `PROFILE_FIX_SUMMARY.md` | одноразовый отчёт |
| `PROOF_OF_FIX.md` | одноразовая заметка |
| `HELP_COMMAND_DIAGNOSIS.md` | одноразовая диагностика |
| `IMPLEMENTATION_COMPLETE.md` | устаревший «готово»-отчёт |
| `FINAL_IMPLEMENTATION_SPEC.md` | ранний спек, перекрыт кодом |
| `TECHNICAL_AUDIT_REPORT.md` | прошлый аудит (до foundation-серии) |
| `TARGETED_IMPROVEMENTS.md` | устаревший план улучшений |
| `UX_IMPROVEMENTS_COMPLETE.md` | устаревший UX-отчёт |
| `UX_IMPROVEMENTS_PLAN.md` | устаревший UX-план |
| `UX_REDESIGN_PLAN.md` | устаревший UX-план |

Итого перемещено: **17 файлов**. Добавлен `docs/archive/README.md` с пояснением.

---

## 3. Что удалить (DELETE) — ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ

Все перечисленные ниже объекты **не отслеживаются git** (`git ls-files` пуст по
ним) — это локальный мусор, безопасный к удалению, но удаляю только после твоего
«да».

### Мусорные файлы в корне
| Объект | Что это |
|---|---|
| `tatusgit status` | артефакт опечатки команды `git status` (текст ушёл в имя файла) |
| `original_profile.py` | бэкап старой версии профиля (есть в git-истории) |
| `profile_before_fix.py` | бэкап перед фиксом |
| `profile_changes.diff` | разовый дифф фикса профиля |
| `test_profile_import.py` | одноразовый скрипт проверки импорта (не часть тест-сьюта) |

### Пустые/случайные директории в корне
| Объект | Что это |
|---|---|
| `voznya-bot/` | пустая вложенная директория (артефакт клонирования) |
| `voznya-bot-new/` | пустая вложенная директория |
| `voznya-bot-repo/` | пустая вложенная директория |

### Артефакт в коде (отслеживается — удалять с осторожностью)
| Объект | Что это | Рекомендация |
|---|---|---|
| `app/features/profile/handlers.py.new` | черновик, не импортируется | удалить через `git rm` после подтверждения |
| `app/features/quick/` | отключённый пустой роутер (не регистрируется) | оставить или удалить — на твоё усмотрение; не мешает |

> Перед удалением `.py`-бэкапов: их содержимое и так в git-истории старых
> коммитов, потери нет.

---

## 4. Что обновить (UPDATE) — ВЫПОЛНЕНО

| Файл | Изменение |
|---|---|
| `README.md` | полностью переписан: архитектура Bot→PostgreSQL→Website→Admin, списки «что есть» / «foundation-only», миграции (HEAD 0014), быстрый запуск, карта документации |
| `AGENTS.md` | убрано устаревшее описание «greenfield stub»; синхронизировано с реальным состоянием (реализованные vs foundation системы, HEAD миграции, гочи) |
| `INVENTORY_FOUNDATION.md` | статус → **FOUNDATION ONLY** (нет рантайм-кода) |
| `SHOP_FOUNDATION.md` | статус → **FOUNDATION ONLY** |
| `GIFT_FOUNDATION.md` | статус → **FOUNDATION ONLY** |
| `MMR_FOUNDATION.md` | статус → **РЕАЛИЗОВАНО** (роутер + хуки + миграция 0014) |
| `REPUTATION_FOUNDATION.md` | статус → **РЕАЛИЗОВАНО** (роутер + миграция 0013) |

Статусы проверены против кода: MMR и reputation имеют зарегистрированные роутеры в
`app/features/__init__.py`; inventory/shop/gifts — только модели, ни один
репозиторий/сервис/хендлер их не использует.

---

## 5. Новый список основной документации проекта

После уборки актуальная документация выглядит так:

```
Корень/
├── README.md                      ← точка входа, реальное состояние
├── AGENTS.md                      ← гайд для AI-агентов
├── CHANGELOG.md                   ← история версий
├── PROJECT_STATE_REPORT.md        ← полный аудит состояния
├── REPOSITORY_CLEANUP_REPORT.md   ← этот документ
├── FOUNDATION_STATUS.md           ← auth/модели/расширения
├── ADMIN_PLATFORM.md              ← RBAC + audit (реализовано)
├── MMR_FOUNDATION.md              ← РЕАЛИЗОВАНО
├── REPUTATION_FOUNDATION.md       ← РЕАЛИЗОВАНО
├── INVENTORY_FOUNDATION.md        ← FOUNDATION ONLY
├── SHOP_FOUNDATION.md             ← FOUNDATION ONLY
├── GIFT_FOUNDATION.md             ← FOUNDATION ONLY
├── COMBOT_IMPORT_PLAN.md          ← импорт Combot
├── COMBOT_MIGRATION.md            ← миграция Combot
├── MINI_APP_PLAN.md               ← план Mini App (не реализован)
├── DELETION_ARCHITECTURE.md       ← сервис удаления
├── DEBUG_INSTRUCTIONS.md          ← опер. шпаргалка
├── VPS_DIAGNOSTIC_COMMANDS.md     ← опер. шпаргалка
├── VPS_RESTART_INSTRUCTIONS.md    ← опер. шпаргалка
├── APPROVED_CHANGES.md            ← журнал согласований
├── docs/
│   ├── ECONOMY.md                 ← баланс экономики
│   └── archive/                   ← 17 устаревших отчётов (история)
```

---

## Готовность к локализации

Репозиторий приведён в порядок: документация отражает реальность, статусы систем
честные, устаревшие отчёты убраны в архив. После подтверждения удаления мусора
из §3 можно начинать этап локализации с чистой базы.

Напоминание для локализации: пользовательские тексты сейчас захардкожены в
`app/settings/texts.py` (RU-only), отдельного i18n-слоя нет — это будет основная
точка работы.
