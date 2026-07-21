# Changelog

Все примечательные изменения проекта kiborg будут задокументированы в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/) и проект следует [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

---

## [1.0.0] — 2026-07-21

**Production-ready релиз kiborg:** автономный агент, который приносит свежие идеи по расписанию, судит их советом моделей, доставляет в инбокс. Работает без постоянного присмотра, с наблюдаемостью и аварийным восстановлением.

### Phase 1 — Рефакторинг монолитов

**Модульная архитектура wiring.py и harvest.py:**
- Разбили `wiring.py` (465 строк) на 7 подмодулей: `wiring_collect`, `wiring_ideate`, `wiring_council`, `wiring_finish`, `wiring_scrub`, `wiring_builder`, `wiring_runtime`. Фасад `wiring.py` (85 строк) с реэкспортом.
- Разбили `harvest.py` (432 строки) на 4 подмодуля: `harvest_env`, `harvest_gate`, `harvest_log`, `harvest_runner`. Фасад `harvest.py` (123 строки) с реэкспортом.
- Централизовали path-bootstrap в единый `cyborg/bootstrap_paths.py` — теперь `import harvest` работает автономно без предварительного `import wiring`.
- Создали единый `cyborg/config.py` для файловых путей и констант — источник истины (DATA, runs.md, state.json, BACKUPS_DIR, MAX_LOG_ENTRIES, MAX_BACKUPS, и т.д.).
- Все 453 теста проходят без изменений (сохранены mutable aliases на фасадах для патч-таргетов).

### Phase 2 — Hardening (наблюдаемость и надёжность)

**Healthcheck и алертинг:**
- Добавили `/api/health` эндпоинт в `panel/serve.py` — возвращает `{ok, llm.available, state_json.{ok,error}, sources.down, last_run}`.
- Реализовали опциональный Telegram-алертинг (`cyborg/alerts.py`) — при `brain_down` или `dropped_stub>0` отправляет в Telegram Bot API (stdlib urllib) или логирует в stdout с префиксом `[ALERT]`.

**Логирование и бэкапы:**
- Ротация `runs.md` — обрезка до `config.MAX_LOG_ENTRIES` (1000) через atomic write.
- Резервное копирование `state.json` + `seen_items.json` перед каждым прогоном (`cyborg/backup.py`) — хранение последних MAX_BACKUPS (10) копий.
- CLI `cyborg/restore_backup.py` — интерактивное восстановление из бэкапов с pre-страховой копией текущего state.

**Документация:**
- `cyborg/ORGANS_API.md` — контракт 8 органов (inputs/outputs/side-effects), снимок на 2026-07-21.

### Phase 3 — Production Readiness

**Автоматический запуск:**
- `deployment/cron_wrapper.sh` — bash-обёртка для Linux/WSL cron.
- `deployment/task_scheduler.xml` — готовый XML для импорта в Windows Task Scheduler.
- `deployment/README.md` — инструкция по настройке автозапуска на обеих ОС.

**Операционный регламент:**
- `RUNBOOK.md` — что делать при типовых сбоях (All LLMs unavailable, state.json corrupted, stale state_lock, источник down), процедура обновления кода, контакты.

**Улучшения надёжности:**
- `ensure_data_dirs()` в `bootstrap_paths.py` — создаёт `cyborg/data/`, `idea_engine/data/`, `cyborg/data/backups/` на свежем клоне.
- `state_lock` timeout warning — `wiring_collect._collect_locked` теперь логирует `[warn] state_lock timeout` при timeout (yielded bool captured).
- Graceful shutdown — SIGTERM/SIGINT обработчики в `panel/serve.py` и KeyboardInterrupt handler в `harvest_runner.py` (корректная остановка без orphaned процессов).

**Stress-тестирование:**
- `stress/stress_test_harvest.py` — нагрузочный тест N прогонов с моками (без LLM/сети), замеры времени/памяти. Вне CI (директория `stress/` не коллекционируется `run_tests.py`).

**Документация секретов:**
- `deployment/llm_keys.env.example` — шаблон для LLM ключей (CLOSEROUTER_API_KEY, SAMBANOVA_API_KEY, GEMINI_API_KEY, и т.д.).
- Обновление `README.md` — секция «Переменные окружения» с описанием `KIBORG_ALERT_TOKEN`, `KIBORG_ALERT_CHAT_ID`, `KIBORG_SLEEP_ORCHESTRA`, `PYTHONUNBUFFERED`.
- Обновление `CONTRIBUTING.md` — инструкция для новых контрибьюторов по настройке `llm_keys.env`.

**Релизное:**
- `CHANGELOG.md` — этот файл (публичный релиз-лог, в отличие от gitignored `.brain/changelog.md`).
- Тег `v1.0.0` будет проставлен после merge PR в master.

### Критерии приёмки

- ✅ Админ по `deployment/README.md` настраивает автозапуск harvest каждые 30 мин на своей ОС.
- ✅ `RUNBOOK.md` даёт чёткий ответ на любой типовой инцидент.
- ✅ `/api/health` возвращает структурированный JSON.
- ✅ Graceful shutdown не оставляет orphaned процессов.
- ✅ Все 473+ тестов зелёные (cyborg 287 + idea_engine 121 + panel 65 + новые 3).
- ✅ ruff/black чистые.

### Сознательные остатки

- **filelock НЕ внедрён** — frozen core (`store.state_lock`) использует O_EXCL+polling, stdlib-only.
- **Python-traceback алертинг НЕ реализован** — только семантика (`brain_down`, `dropped_stub`), по решению Phase 2.
- **Rename ENV ALERT_* → TELEGRAM_ALERT_* НЕ сделан** — Phase 2 shipped `KIBORG_ALERT_*`, код источник истины.
- **Стресс-тест не запущен на N=50** — требует отладки импортов в Windows окружении, placeholders оставлены.

### Миграция с v0.x

Для обновления с предыдущих версий:

1. `git pull` — получить все изменения.
2. Скопировать `deployment/llm_keys.env.example` → `llm_keys.env` (если ещё нет).
3. `python run_tests.py` — убедиться что тесты проходят.
4. Настроить автозапуск через `deployment/README.md` (Linux/WSL или Windows).

### Благодарности

- **Пользователи,** тестировавшие альфа-версии и сообщившие о багах.
- **Сообщество,** вдохновившее на создание agents-as-code подхода.

---

[Unreleased]: https://github.com/404stillhere/kiborg/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/404stillhere/kiborg/compare/v0.0.0...v1.0.0
