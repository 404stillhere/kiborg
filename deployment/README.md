# Deployment — Автоматический запуск kiborg по расписанию

Эта папка содержит скрипты для настройки автоматического запуска `harvest.py` на операционных уровнях (cron для Linux/WSL, Task Scheduler для Windows).

## Что это даёт

- Автоматический прогон `cyborg/harvest.py` каждые N минут (по умолчанию 30)
- Если нет новых данных — harvest завершается без ошибки (gate skip)
- Если есть — прогон отрабатывает, результат виден:
  - В `cyborg/data/runs.md` (краткая сводка прогонов)
  - В `panel/serve.py → /api/health` (здоровье системы)
  - В `cyborg/data/cron_<timestamp>.log` (полный stdout/stdstderr)

## Linux / WSL — настройка через cron

### 1. Сделайте скрипт исполняемым

```bash
chmod +x deployment/cron_wrapper.sh
```

### 2. Откройте crontab

```bash
crontab -e
```

### 3. Добавьте задачу

**Пример 1 — каждые 30 минут:**

```cron
*/30 * * * * /path/to/kiborg/deployment/cron_wrapper.sh
```

**Пример 2 — каждый час:**

```cron
0 * * * * /path/to/kiborg/deployment/cron_wrapper.sh
```

**Пример 3 — каждые 5 минут (агрессивно, только для теста):**

```cron
*/5 * * * * /path/to/kiborg/deployment/cron_wrapper.sh
```

### 4. Сохраните и выйдите

Cron автоматически перезагрузит конфиг.

### 5. Проверка логов

```bash
tail -f cyborg/data/cron_*.log
```

## Windows — настройка через Task Scheduler

### 1. Откройте Планировщик задач

Win+R → `taskschd.msc` → Enter

### 2. Импортируйте задачу

- Action → Import Task
- Выберите `deployment/task_scheduler.xml`

### 3. Замените placeholder'ы (если не сделали заранее)

Откройте Imported Task → вкладка "Actions" и "Triggers":

- **Command** — путь к `pythonw.exe` (без консоли), например:
  - `C:\Python312\pythonw.exe` (официальный Python)
  - `C:\Users\<User>\AppData\Local\Programs\Python\Python312\pythonw.exe` (Windows Store)
- **Arguments** — путь к `harvest.py`, например:
  - `M:\projects\kiborg\cyborg\harvest.py 1` (где `1` — число прогонов за вызов)
- **Start in** — рабочая директория проекта:
  - `M:\projects\kiborg`

### 4. Проверьте триггер

- По умолчанию: каждые 30 минут
- Можно изменить на вкладке "Triggers"

### 5. Сохраните задачу

Нажмите OK.

### 6. Ручной запуск (тест)

- Найдите задачу "Kiborg Auto Harvest" в списке
- Правый клик → Run

### 7. Проверка логов

- Логи: `cyborg\data\cron_*.log`
- Панель health: http://127.0.0.1:8737/api/health

## Как проверить, что автозапуск работает

### 1. Посмотрите логи

```bash
ls -la cyborg/data/cron_*.log | tail -1
```

Должен быть свежий файл (время последнего запуска по расписанию).

### 2. Проверьте runs.md

```bash
tail -5 cyborg/data/runs.md
```

Должны быть новые строки после включения автозапуска.

### 3. Проверьте health

```bash
curl http://127.0.0.1:8737/api/health
```

В поле `last_run.running` должно быть `false` (последний прогон завершён), а `sources` — без ошибок.

## Типовые проблемы

### "Нет новых данных" — это норма

Если gate (проверка источников) не нашёл новых элементов, harvest пропускает итерацию с сообщением "нет свежих данных" — это не ошибка. Лог покажет `skip reason: source signature matches`, но exit code будет 0.

### "Permission denied" на cron_wrapper.sh

```bash
chmod +x deployment/cron_wrapper.sh
```

### "python: command not found"

Укажите полный путь к интерпретатору в `cron_wrapper.sh` (переменная `PYTHON`), например:

```bash
PYTHON="/usr/bin/python3"
```

или активируйте venv:

```bash
KIBORG_VENV="/path/to/venv"
```

### "ModuleNotFoundError: No module named 'cyborg'"

Проверьте, что `cron_wrapper.sh` делает `cd "$PROJECT_ROOT"` перед запуском `python cyborg/harvest.py`. Если не делает — исправьте путь в скрипте или в crontab (вызывайте через абсолютный путь к wrapper).

## Отключение автозапуска

### Linux

```bash
crontab -e
# закомментируйте или удалите строку с cron_wrapper.sh
```

### Windows

- Откройте Task Scheduler
- Правый клик по "Kiborg Auto Harvest" → Disable (или Delete)

## Следующие шаги

После настройки автозапуска:

1. Посмотрите `RUNBOOK.md` (в корне проекта) — операционный регламент, что делать при типовых сбоях.
2. Настройте алертинг: переменные окружения `KIBORG_ALERT_TOKEN` и `KIBORG_ALERT_CHAT_ID` (см. `README.md`).
3. Проверьте, что бэкапы создаются: `ls -la cyborg/data/backups/` (автоматически перед каждым прогоном).

---

**Где смотреть логи:** `cyborg/data/cron_*.log`  
**Где смотреть прогоны:** `cyborg/data/runs.md`  
**Где проверить здоровье:** http://127.0.0.1:8737/api/health (панель должна быть запущена `panel/serve.py`)
