"""Единая конфигурация путей и файловых констант kiborg.

Источник истины для всех файловых путей, data-файлов и конфигурации источников. Раньше пути
были размазаны по фасадам: `DATA` объявлялось 5 раз одинаково (harvest/run/seen_items +
4 через `_panel_config`), `runs.md` склеивалось в 3 местах (run/harvest_log/serve),
`source_status.json` — в 2 (harvest и serve), `organs.json` — в 2 (registry и serve).
Теперь всё здесь — одно место для правок и обзора.

АРХИТЕКТУРА с фасадами (wiring/harvest/serve): фасады делают **mutable-алиасы** на module
level (`STATE_FILE = config.HARVEST_STATE_FILE`). Это нужно, потому тесты патчат константы
через фасад (`harvest.STATE_FILE = tmp`), а живой код в подмодулях (harvest_gate, wiring_finish,
...) читает их ЧЕРЕЗ ФАСАД (`import harvest; harvest.STATE_FILE`), а не напрямую из config.
`from config import X as Y` создаёт тот же module global Y — патч `facade.Y = tmp` переписывает
его, и подмодуль видит новое значение. НЕ переключать подмодули на `import config; config.X` —
это сломало бы patch-target'ы (см. тесты test_registry/test_harvest/test_wiring/test_serve).

Что НЕ живёт тут (сознательные остатки, см. CONTRIBUTING/loose-ends):
  - feeds.PATH/folders.PATH/direction.PATH/council_config.PATH/seen_items.PATH — каждый файл
    уникален и патчится на своём модуле; перенос дал бы +5 алиасов без выгоды.
  - 5 ENV-имён в frozen-модулях (KIBORG_LLM_KEYS, KIBORG_ASK_LLM_JS, ...) — frozen core не трогаем.
  - serve.RUN/_PROC/_AUTO — мутабельный runtime-state (dict), не константы.
  - observe_sources.WHERE/ORDER/_ITEM_PAUSE — UI-конфиг наблюдателя, локальная ответственность.
"""

import os

# === КОРНЕВЫЕ ПУТИ ПРОЕКТА (от __file__ = cyborg/config.py — относительно, любая ОС) ===
# Раньше были захардкожены абсолютными Windows-путями (M:/projects/kiborg/...) — ломали CI
# на Linux. Теперь вычисляются от своего __file__: перенос проекта не инвалидирует пути.
CYBORG_DIR = os.path.dirname(os.path.abspath(__file__))  # .../kiborg/cyborg
PROJECT_ROOT = os.path.dirname(CYBORG_DIR)  # .../kiborg
IDEA_ENGINE_DIR = os.path.join(PROJECT_ROOT, "idea_engine")  # .../kiborg/idea_engine
PANEL_DIR = os.path.join(PROJECT_ROOT, "panel")  # .../kiborg/panel

# === DATA-КАТАЛОГИ ===
CYBORG_DATA_DIR = os.path.join(CYBORG_DIR, "data")
IDEA_ENGINE_DATA_DIR = os.path.join(IDEA_ENGINE_DIR, "data")

# === DATA-ФАЙЛЫ cyborg/ ===
# Лог прогонов (append-only) — пишут run.py и harvest_log._log, читает panel/serve._read_runs.
# Раньше склеивался ВРУЧНУЮ в 3 местах; теперь единая константа.
RUNS_MD = os.path.join(CYBORG_DATA_DIR, "runs.md")
# Живой per-source статус для пульта — пишет harvest_gate._persist_status, читает serve._read_source_status.
SOURCE_STATUS_FILE = os.path.join(CYBORG_DATA_DIR, "source_status.json")
# Gate-отпечаток ленты («есть что новое?») — пишут/читают harvest_gate._save_sig/_last_sig.
# Патчится в тестах: `harvest.STATE_FILE = tmp` (фасадный алиас).
HARVEST_STATE_FILE = os.path.join(CYBORG_DATA_DIR, "harvest_state.json")
# Курсор ротации finish_step («доделай») — пишет/читает wiring_finish._run_finish.
# Патчится в тестах: `wiring._CURSOR_FILE = tmp` (фасадный алиас).
CURSOR_FILE = os.path.join(CYBORG_DATA_DIR, "finish_cursor.json")
# Своя копия tg-сессии (pyrogram/SQLite) — не та, что живой darbot-бот держит открытой.
# Патчится в тестах: `harvest._KIBORG_TG_SESSION = tmp` (фасадный алиас).
KIBORG_TG_SESSION = os.path.join(CYBORG_DATA_DIR, "kiborg_tg.session")

# === DATA-ФАЙЛЫ idea_engine/ (read-only со стороны cyborg — доставляет/читает через deliver) ===
INBOX_MD = os.path.join(IDEA_ENGINE_DATA_DIR, "inbox.md")  # инбокс идей для человека
IE_STATE_JSON = os.path.join(IDEA_ENGINE_DATA_DIR, "state.json")  # Store idea_engine (счётчик открытых)

# === ВНЕШНИЕ АРТЕФАКТЫ (только на прод-машине юзера; на CI их нет,代码 ловит исключения) ===
# Backlog проектов для finish_step «доделай». Читается wiring_finish → finish_step.run(recon_path=...).
RECON_FILE = "M:/projects/panelofprojects/recon.json"
# Каталог органов (89 карточек) — информационный слой для registry/serve. На CI нет.
ORGANS_CATALOG = "M:/projects/_shared/organs.json"
# Чужой .env darbot — TG_API_ID/TG_API_HASH оттуда (read-only, не трогаем чужой файл).
DARBOT_ENV = "M:/projects/darbot/.env"

# === КОНФИГ ИСТОЧНИКОВ ===
# Сколько заголовков тянуть за прогон СУММАРНО (бюджет делится между источниками в collect_source).
# Дефолт органа = 8; 105 // 21 канал = 5 свежих постов с каждого → ~105 семян вместо 21.
# Больше и разнообразнее сырья для ideate, глубже churn для гейта. Режим «максимум качества».
SOURCE_N = 105

# Каналы под тематику kiborg (тех/AI/pet-проекты) — НЕ список darbot (тот про новости/политику).
# @tproger (стартовый, подтверждён живым смоуком 2026-07-12) + 20 AI-каналов из папки юзера
# (t.me/addlist/gUpAozY8_SI0ZTVi), разрешена read-only (chatlists.CheckChatlistInvite) 2026-07-12.
# Все настоящие, список подтверждён живым 2026-07-13. Список длиннее бюджета n — _telegram()
# берёт случайную выборку каждый прогон (ротация по времени).
TELEGRAM_CHANNELS = [
    "@tproger",
    "@ai_machinelearning_big_data",
    "@unitool",
    "@llm_under_hood",
    "@gpt_news",
    "@hiaimedia",
    "@openai_fan",
    "@data_secrets",
    "@machinelearning_interview",
    "@data_analysis_ml",
    "@neuro_code",
    "@neuraldvig",
    "@aitshnya",
    "@seeallochnaya",
    "@gptpublic",
    "@ai_newz",
    "@notboring_tech",
    "@lovedeathtransformers",
    "@machinelearning_ru",
    "@boris_again",
    "@techsparks",
]

# Источники, ЛИЧНО проверенные юзером (не «бета»). Пульт метит непроверенные «β». Пока — telegram
# (каналы юзер курировал сам, @tproger подтверждён смоуком) и files (свои папки юзера).
USER_VERIFIED_SOURCES = {"telegram", "files"}

# === НАСТРОЙКИ ===
# Таймаут сериализации tg-сессии (сек): гейт-проба/живой прогон/CLI могут пересечься на одном
# .session ('database is locked'). > фетча (~90с), чтобы ждущий дождался.
# Патчится в тестах: `wiring._TG_LOCK_TIMEOUT = 0.2` (фасадный алиас) для быстрых тестов.
TG_LOCK_TIMEOUT = 130.0
# Порог «протухания» lock-файла tg-сессии (мин): после аварийного падения процесса
# <session>.lock остаётся на диске, и каждый следующий прогон честно ждёт полный
# TG_LOCK_TIMEOUT (130с), прежде чем пройти без лока. Если lock старше этого порога —
# он гарантированно «зависший» (живой прогон телеграма укладывается в фетч ~90с << порога),
# и _collect_locked удаляет его ПЕРЕД захватом, не тратя время на ожидание.
# Патчится в тестах: `wiring._STALE_LOCK_MAX_AGE = ...` (фасадный алиас, минуты → секунды).
STALE_LOCK_MAX_AGE_MINUTES = 30
# Folder'ы, которые режим «доделай» (finish_step) не толкает (пусто = не фильтровать). Knob.
SKIP_FOLDERS = []
# ENV-имя для усыпления 7-модельного оркестра (читается в harvest_env.wire_council).
# Остальные 5 KIBORG_* ENV-имён живут в frozen-модулях (advisors/ask_llm/keychain) — не выносим.
SLEEP_ORCHESTRA_ENV = "KIBORG_SLEEP_ORCHESTRA"

# === АЛЕРТИНГ (опциональный, через Telegram Bot API) ===
# Если при прогоне случился семантический сбой (out['brain_down'] / много dropped_stub),
# harvest_log._log зовёт alerts.maybe_alert(level, msg). Когда в окружении заданы ОБА ENV —
# алерт уходит в Telegram (urllib, без новой зависимости). Нет ENV — логируется в stdout с
# пометкой [ALERT]. Токен бота храним в ENV запуска (не в llm_keys.env — это не LLM-ключ).
# Задать: export KIBORG_ALERT_TOKEN=123:abc  export KIBORG_ALERT_CHAT_ID=987654321
ALERT_TOKEN_ENV = "KIBORG_ALERT_TOKEN"
ALERT_CHAT_ENV = "KIBORG_ALERT_CHAT_ID"
# Таймаут HTTP-запроса в TG (алертинг не должен надолго блокировать прогон). При ошибке/timeout
# молча падает на print — прогон продолжается.
ALERT_HTTP_TIMEOUT = 10.0

# Ротация runs.md: после записи нового прогона harvest_log._rotate_if_needed обрезает файл
# до последних MAX_LOG_ENTRIES строк (1 прогон = 1 строка, формат построчный — см. serve._read_runs).
# Раньше runs.md рос без огранички; сейчас — скользящее окно. 1000 записей ≈ 30 дней при
# авто-сборе раз в 45 мин, или много месяцев ручных прогонов.
MAX_LOG_ENTRIES = 1000

# === РЕЗЕРВНОЕ КОПИРОВАНИЕ state.json + seen_items.json ===
# harvest_runner.main() перед прогоном зовёт backup.backup_state() — копирует оба файла в
# BACKUPS_DIR/<TS>/. Ротация: храним последние MAX_BACKUPS копий (по умолчанию 10). Восстановление —
# через CLI-утилиту cyborg/restore_backup.py. Бэкап только при авто-сборе (ручной run.py не триггерит —
# меньше шума; state.json всё равно под state_lock, гонки нет).
BACKUPS_DIR = os.path.join(CYBORG_DATA_DIR, "backups")
MAX_BACKUPS = 10

# === PANEL ===
PANEL_PORT = 8737  # локальный HTTP пульт, слушает ТОЛЬКО 127.0.0.1
RUN_TIMEOUT_SEC = 1200  # watchdog на один прогон (сек) — снимает зависший subprocess
# Рубильник авто-режима пульта (JSON с интервалом/last-run). Патчится в тестах: `serve.AUTO_FILE = tmp`.
AUTO_JSON = os.path.join(PANEL_DIR, "auto.json")
# Feature-lab статус фич (внутренний). Патчится в тестах: `serve.LAB_ROUTER = tmp`.
LAB_ROUTER_FILE = os.path.join(PROJECT_ROOT, ".feature-lab", "router.json")
