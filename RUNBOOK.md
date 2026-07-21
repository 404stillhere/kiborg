# RUNBOOK — Операционный регламент kiborg

**Цель:** Дать администратору чёткий ответ на вопрос "что делать, когда что-то пошло не так". Документ предполагает, что kiborg уже запущен в production (автозапуск настроен через `deployment/`, панель работает на `http://127.0.0.1:8737`).

## Архитектура (кратко)

**kiborg — агент, который каждые N минут (по умолчанию 30) делает:**

1. Проверяет источники (Telegram, HN, Reddit) — есть ли новые элементы.
2. Если нет — пропускает итерацию (gate skip).
3. Если есть — прогоняет pipeline:
   - `collect_source` → соберает элементы из источников
   - `ideate` → генерирует идеи (через LLM)
   - `rank_ideas` → оценивает идеи (через совет/судью)
   - `readability_gate` → проверяет читаемость
   - `scrub_secrets` → удаляет секреты
   - `deliver` → доставляет идеи в инбокс (`idea_engine/data/state.json`)
4. Пишет результат в `cyborg/data/runs.md` (краткая сводка).

**Два entrypoint'а:**

- `python cyborg/harvest.py [N]` — автоматический прогон (gate skip, N итераций, max 50). Используется cron/Task Scheduler.
- `python cyborg/run.py "<цель>"` — ручной прогон (один проход, полный trace в stdout). Используется пультом (`panel/serve.py`).

**Детали контрактов органов** — см. `cyborg/ORGANS_API.md`.

## Ключевые файлы

| Файл | Что внутри | Зачем смотреть |
|---|---|---|
| `cyborg/llm_keys.env` | API ключи LLM провайдеров | Ротация ключей при «All LLMs unavailable» |
| `cyborg/data/runs.md` | Сводка прогонов (1 строка = 1 прогон) | Быстрая диагностика последнего прогона |
| `cyborg/data/backups/` | Резервные копии state.json + seen_items.json | Восстановление при порче |
| `idea_engine/data/state.json` | Инбокс (состояние идей, статусы, курсор) | Основное состояние, при порче → restore |
| `cyborg/data/source_status.json` | Статус последнего опроса источников | Ошибки источников (Telegram, HN, Reddit) |
| `panel/auto.json` | Настройки авто-режима пульта | Вкл/выкл авто-прогона через пульт |

## Healthcheck — как проверить здоровье

### Через браузер

```
http://127.0.0.1:8737/api/health
```

### Через curl

```bash
curl http://127.0.0.1:8737/api/health
```

### Интерпретация полей

```json
{
  "ok": true,
  "llm": {"available": true},
  "state_json": {"ok": true, "error": null},
  "sources": {
    "down": ["telegram"],
    "status": {
      "sources": {
        "telegram": {"last_check": "...", "error": "timeout"},
        "hn": {"last_check": "..."}
      }
    }
  },
  "last_run": {"rc": null, "running": false},
  "locks": {"recent_timeouts": 0, "window_minutes": 60}
}
```

- `ok` → `true` если всё здорово, `false` если есть проблема
- `llm.available` → `true` если есть хотя бы один рабочий LLM ключ
- `state_json.ok` → `true` если `state.json` читается и валиден
- `locks.recent_timeouts` → сколько таймаутов `state_lock` было за последний час
  (`window_minutes`). После внедрения stale-lock-cleanup это РЕДКОСТЬ — `>0` значит,
  что живой конкурент реально держал лок дольше `TG_LOCK_TIMEOUT` (130с). Прогон при
  этом ПРОШЁЛ (без лока), на `ok` НЕ влияет — это диагностическая метрика, не сбой.
- `sources.down` → список источников с ошибками (например, `["telegram"]` при timeout)
- `last_run.running` → `true` если сейчас идёт прогон (пульс в пульте), `false` если idle
- `last_run.rc` → exit code последнего прогона (если был)

## Типовые сбои — что делать

### 1. «All LLMs unavailable» — все LLM промолчали

**Симптомы:**
- `/api/health` → `llm.available: false`
- `runs.md` → строки с `мозг недоступен — все LLM промолчали, инбокс пуст`
- Алерт в Telegram (если настроен): `[kiborg][CRITICAL] мозг недоступен`

**Причины:**
- Нет рабочих ключей в `llm_keys.env`
- Баланс на провайдере закончился
- Сеть недоступна
- Провайдер перегружен (503/429)

**Что делать:**
1. Проверить `llm_keys.env` — есть ли там ключи:
   ```bash
   cat cyborg/llm_keys.env | grep -E "API_KEY|_TOKEN"
   ```
2. Проверить баланс на провайдере (если доступен).
3. Проверить сеть:
   ```bash
   curl -I https://api.closerouter.dev/v1/chat/completions
   ```
4. Если ключи протухли — ротировать:
   - Получить новые ключи у провайдера
   - Обновить `llm_keys.env`
   - Перезапустить `panel/serve.py` (если запущена)
5. Если сеть — временная проблема, дождаться восстановления.

### 2. «state.json corrupted» — state.json повреждён

**Симптомы:**
- `/api/health` → `state_json.ok: false, error: "..."`
- Прогон падает с ошибкой `JSONDecodeError` или `KeyError`
- Пульт не показывает идеи в инбоксе

**Что делать:**
1. Восстановить из последнего бэкапа:
   ```bash
   python cyborg/restore_backup.py
   ```
2. Следовать интерактивным подсказкам (выберите бэкап, подтвердите восстановление).
3. Проверить `state.json` после восстановления:
   ```bash
   cat idea_engine/data/state.json | jq '.ideas | length'
   ```
4. Если все бэкапы повреждены — последний resort: удалить `state.json`, kibорг создаст новый пустой инбокс (все идеи потеряны, но система продолжит работать).

### 3. «runs.md переполнен» — runs.md слишком большой

**Симптомы:**
- `runs.md` занимает много места (Mb)
- Открытие файла тормозит

**Что делать:**
- Ничего — **ротация включена автоматически**. После каждого прогона `runs.md` обрезается до `config.MAX_LOG_ENTRIES` (по умолчанию 1000 строк).
- Если нужно принудительно очистить:
  ```bash
  tail -n 100 cyborg/data/runs.md > cyborg/data/runs.md.tmp && mv cyborg/data/runs.md.tmp cyborg/data/runs.md
  ```
  (сохранит последние 100 строк).

### 4. «Прогон завис (stale state_lock)» — stale lock-файл

**Симптомы:**
- Прогон висит дольше 5 минут
- `/api/health` → `last_run.running: true` долгое время
- В логах есть `[warn] state_lock timeout` (если включён Коммит 4 Phase 3)

**Причина:**
- Процесс, держащий `state.json.lock`, крашнулся и не освободил лок
- Лок-файл `<path>.lock` остался

**Что делать:**
1. Найти lock-файл:
   ```bash
   ls -la idea_engine/data/state.json.lock
   ls -la cyborg/data/*.lock
   ```
2. Убить процесс, если ещё жив (по PID, если можно):
   ```bash
   ps aux | grep python
   ```
3. Удалить lock-файл:
   ```bash
   rm idea_engine/data/state.json.lock
   rm cyborg/data/*.lock
   ```
4. Следующий прогон пройдёт нормально.

### 5. «Источник down» — Telegram/HN/Reddit недоступен

**Симптомы:**
- `/api/health` → `sources.down: ["telegram"]`
- `source_status.json` → `{"error": "timeout"}`

**Что делать:**
- Если временная проблема — дождаться восстановления
- Если постоянная — проверить сеть/креды (для Telegram: сессия `cyborg/data/<session>.session` должна быть валидной)
- Можно временно исключить источник из `config.TELEGRAM_CHANNELS` / `harvest.feeds.enabled` (код, но лучше починить источник)

## Обновление кода — как деплоить новую версию

### 1. Получить изменения

```bash
git pull
```

### 2. Проверить тесты

```bash
python run_tests.py
```

Все 473+ тестов должны пройти.

### 3. Перезапустить панель

Если `panel/serve.py` запущена:

```bash
# Найти процесс
ps aux | grep "panel/serve.py"

# Убить
kill <PID>

# Запустить
cd /path/to/kiborg/panel
python serve.py &
```

### 4. Понаблюдать за первым прогоном

- Открыть пульт: http://127.0.0.1:8737
- Посмотреть `/api/health` после прогона
- Проверить `runs.md` (должна быть новая строка)

## Контакты

- **Алертинг:** Telegram chat, указанный в `KIBORG_ALERT_CHAT_ID` (см. `README.md` → «Переменные окружения»)
- **Для добавления нового чата:** обновите `KIBORG_ALERT_CHAT_ID` и перезапустите `panel/serve.py`

## Стресс-тест (пройден 2026-07-21)

**Что проверяли:** 50 прогонов harvest с моками (LLM отключён, collect_source возвращает 3 stub items, state/runs/seens redirect'ы в tmpdir).

**Машина:** Windows 10 x64, проект на `M:/projects/kiborg/`, Python 3.12.

**Результат:**

| Метрика | Значение | Оценка |
|---|---|---|
| Среднее время прогона | **120.9 ms/итерация** | ✅ отлично (<500ms цель) |
| Пиковый рост памяти | **2792 KB (~2.8 MB)** | ✅ стабилен (без утечек) |
| Ошибок в `runs.md` | **0** | ✅ чист |
| Суммарное время 50 прогонов | **~6 сек** | ✅ быстро |
| Память после 50 прогонов | та же, что после 10-го | ✅ нет утечки |

**Вывод:** утечек памяти и деградации производительности НЕ обнаружено. Система стабильна на 50+ прогонах подряд.

**Команда для повторного запуска:**
```bash
python stress/stress_test_harvest.py 50
```

### ⚠️ Узкое место, найденное при прогоне

**Bottleneck: stale state_lock на telegram-сессии → +130s к каждой итерации**

Первый прогон (до изоляции мока) показал **130553 ms/итерация** (вместо 120 ms) из-за того, что `_collect_locked` реально пытался взять `state_lock` на `cyborg/data/kiborg_tg.session`, где висел stale lock-файл от крашнувшегося процесса 7 дней назад:

```
cyborg/data/kiborg_tg.session.lock   (0 байт, от 2026-07-14 22:50)
```

State_lock имеет `timeout=130.0` (config.TG_LOCK_TIMEOUT) — каждая итерация ждёт все 130 секунд, потом проходит без лока (best-effort смягчение гонки в frozen core).

**Что сделано:**
1. Stale lock удалён вручную: `rm cyborg/data/kiborg_tg.session.lock`
2. Stress-тест изолирован от telegram_session (`env.pop("telegram_session", None)` в `stress_test_harvest.py`).

**Что НЕ решено (задачи на следующий спринт — см. ниже):**

- Stale lock может появиться снова при любом краше процесса во время telegram-сессии — автоматической очистки нет.
- Frozen `state_lock` в `idea_engine/store.py` НЕ очищает stale locks (намеренно, чтобы не трогать чужой лок при обходе по timeout).
- Мониторинг этого warning'а (`[warn] state_lock timeout`) сейчас только в stdout логах — в `/api/health` не попадает.

**Как заметить проблему в проде:** в логах появятся строки `[warn] state_lock timeout (130s) на <path>`. См. RUNBOOK секцию «Прогон завис (stale state_lock)» для ручного лечения.

---

## Задачи на следующий спринт (найдено stress-тестом)

### ✅ P1 — Stale lock cleanup (РЕШЕНО 2026-07-21, коммит `2e9cf6a`)
- **Проблема:** краш процесса во время telegram-сессии оставляет `.lock` файл навсегда.
- **Где:** `cyborg/data/kiborg_tg.session.lock`, `idea_engine/data/state.json.lock`.
- **Решение:** `_remove_stale_lock()` в `cyborg/wiring_collect.py` — перед захватом `state_lock`
  проверяет mtime lock-файла; старше `STALE_LOCK_MAX_AGE_MINUTES` (30) → сносится как труп.
  Свежий (живой конкурент) не трогается. Порог в `config.py`, mutable-алиас
  `wiring._STALE_LOCK_MAX_AGE` для тестов.
- **Frozen constraint соблюдён:** `store.state_lock` не тронут, lock-имя дублировано (`path + ".lock"`).

### ✅ P2 — state_lock timeout в /api/health (РЕШЕНО 2026-07-21)
- **Проблема:** warning `[warn] state_lock timeout` виден только в stdout, в health не попадал.
- **Решение:** модуль `cyborg/lock_monitor.py` — лёгкий in-memory счётчик (list[float] под
  `threading.Lock`, без файла). `record_timeout()` зовётся из `_collect_locked` рядом с warn,
  `recent_timeouts(minutes=60)` читается из `/api/health` → поле `locks.recent_timeouts`.
- **Per-process:** счётчик живёт в ОП процесса `panel/serve.py` (не persisted; эпемерный
  сигнал «этот час было шумно на локах»). Lazy cleanup устаревших при вызове `recent_timeouts`.

### 🟡 P3 — Auto-recovery в restore_backup
- **Проблема:** при повреждении state.json нужен ручной запуск `restore_backup.py`.
- **Решение:** в `harvest_runner.main()` перед прогоном проверять `state.json` → если повреждён → автоматически восстанавливать из последнего бэкапа + alert.

---

**Дополнительная документация:**
- `cyborg/ORGANS_API.md` — контракты органов
- `README.md` — общий обзор проекта
- `deployment/README.md` — настройка автозапуска
- `CONTRIBUTING.md` — для контрибьюторов
