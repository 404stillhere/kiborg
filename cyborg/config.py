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
# Последний ответивший LLM-провайдер (zai/native/muse-spark/...). Пишет ask_llm, читают
# panel/serve.py и harvest._degrade_note. Файл нужен, потому что панель — отдельный процесс.
LAST_PROVIDER_FILE = os.path.join(CYBORG_DATA_DIR, "last_provider.json")

# === DATA-ФАЙЛЫ idea_engine/ (read-only со стороны cyborg — доставляет/читает через deliver) ===
INBOX_MD = os.path.join(IDEA_ENGINE_DATA_DIR, "inbox.md")  # инбокс идей для человека
IE_STATE_JSON = os.path.join(IDEA_ENGINE_DATA_DIR, "state.json")  # Store idea_engine (счётчик открытых)

# === ВНЕШНИЕ АРТЕФАКТЫ (только на прод-машине юзера; на CI их нет,代码 ловит исключения) ===
# Backlog проектов для finish_step «доделай». Читается wiring_finish → finish_step.run(recon_path=...).
RECON_FILE = os.path.join("M:/", "projects", "panelofprojects", "recon.json")
# Каталог органов (89 карточек) — информационный слой для registry/serve. На CI нет.
ORGANS_CATALOG = os.path.join("M:/", "projects", "_shared", "organs.json")
# Чужой .env darbot — TG_API_ID/TG_API_HASH оттуда (read-only, не трогаем чужой файл).
DARBOT_ENV = os.path.join("M:/", "projects", "darbot", ".env")
# Чужой Python darbot-venv для запуска collect_tg_news.py (read-only, не трогаем чужой venv).
DARBOT_PYTHON = os.path.join("M:/", "projects", "darbot", "venv", "Scripts", "python.exe")
# Путь к файлу LLM-ключей по умолчанию (не в repo; должен быть в .gitignore).
DEFAULT_LLM_KEYS_FILE = os.path.join(PROJECT_ROOT, "llm_keys.env")
# Внешние органы (DarBench / Claude Code API Dual Mode) — дефолтные пути для fallback.
DEFAULT_ASK_LLM_JS = os.path.join("M:/", "projects", "DarBench", "organ.js")
DEFAULT_ORCHESTRA_PY = os.path.join("M:/", "projects", "Claude Code API Dual Mode", "organ.py")
# Корень проектов для Oracle-режима (сканер ищет относительные пути здесь).
DEFAULT_PROJECTS_ROOT = os.path.join("M:/", "projects")

# === КОНФИГ ИСТОЧНИКОВ ===
# Сколько заголовков тянуть за прогон СУММАРНО (бюджет делится между источниками в collect_source).
# Дефолт органа = 8; 105 // 21 канал = 5 свежих постов с каждого → ~105 семян вместо 21.
# Больше и разнообразнее сырья для ideate, глубже churn для гейта. Режим «максимум качества».
SOURCE_N = 105

# === FEEDBACK CORTEX (B4) ===
# Порог активации адаптивных весов: минимум реальных triage-действий для включения.
FEEDBACK_CORTEX_THRESHOLD = 20
# EMA α — скорость адаптации весов (0.02 = медленная, часовой cron, не шум).
FEEDBACK_CORTEX_EMA_ALPHA = 0.02
# Decay-фактор: подтягивание весов к равномерному при пересечении порога событий.
FEEDBACK_CORTEX_DECAY_FACTOR = 0.95
# Период decay: пересечение границы DECAY_EVERY событий запускает decay.
FEEDBACK_CORTEX_DECAY_EVERY = 30
# Минимальный вес советника: никто не выключается полностью.
FEEDBACK_CORTEX_MIN_WEIGHT = 0.15
# Максимальный сдвиг веса за один цикл адаптации (после нормировки сигнала).
FEEDBACK_CORTEX_MARGIN = 0.1
# Число советников (для равномерного распределения). Должно совпадать с len(ALL_ADVISORS).
FEEDBACK_CORTEX_N_ADVISORS = 3
# Максимальное число итераций enforce_min_weight (для сходимости clamp+renormalize).
FEEDBACK_CORTEX_ENFORCE_MAX_ITER = 10

# === ВЕСА СОВЕТНИКОВ (mind / council_weights) ===
# Канонические веса советников при отборе идей. Заданы юзером 2026-07-13.
# Сумма = 1.0. Является источником истины для mind.WEIGHTS и council_weights.DEFAULT_WEIGHTS.
ADVISOR_WEIGHTS = {"ask_llm": 0.39, "orchestra": 0.20, "rank_ideas": 0.41}
# Порядок тай-брейка при равном итоговом балле (по убыванию веса).
ADVISOR_TIE_ORDER = ["rank_ideas", "ask_llm", "orchestra"]

# === ЛЕНТЫ-ИСТОЧНИКИ (feeds / collect_source) ===
# Доступные ленты-источники (порядок = порядок показа тумблеров в пульте). Должен совпадать
# с ключами _SOURCES в idea_engine/organs/collect_source.py минус 'files' (у папок свой блок).
ALL_FEEDS = ["hn", "reddit", "lobsters", "gh_trending", "telegram", "self"]
# Дефолтный набор включённых лент (когда файла настроек ещё нет). Как было в harvest.SOURCES:
# только telegram — лично курированный юзером источник.
DEFAULT_FEEDS = ["telegram"]

# === СОВЕТ СОВЕТНИКОВ (council_config) ===
# Доступные советники при отборе идей. Порядок важен для фильтрации "unknown" в save().
ALL_ADVISORS = ["rank_ideas", "ask_llm", "orchestra"]
# Какие советники включены по умолчанию (когда файла настроек ещё нет).
DEFAULT_ADVISORS_ENABLED = ["rank_ideas", "ask_llm", "orchestra"]

# === ПАРАМЕТРЫ ГЕНЕРАЦИИ ИДЕЙ (genparams) ===
# Дефолтные значения, которые юзер может переопределить в пульте. Централизовано,
# чтобы не дублировать литералы в genparams.py, wiring_ideate.py, wiring_council.py.
DEFAULT_GEN_K = 8
DEFAULT_RANK_KEEP = 3
DEFAULT_SOURCE_N = SOURCE_N  # collect_source бюджет
DEFAULT_READ_MIN_SCORE = 8.0
DEFAULT_KEEP_MIN_SCORE = 0.6

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

# Источники, ЛИЧНО проверенные юзером (не «бета»). Пульт метит непроверенные «β»: telegram
# (каналы юзер курировал сам), files (свои папки) и self (собственный проект kiborg).
USER_VERIFIED_SOURCES = {"telegram", "self", "files"}

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
# ENV-имена (централизовано — единый источник истины, чтобы не дублировать литералы в модулях).
# Осторожно: тесты в test_alerts/test_notify/test_deliver напрямую читают/пишут os.environ["..."] —
# при переименовании здесь их трогать не надо, но новые тесты должны брать имя из config.
SLEEP_ORCHESTRA_ENV = "KIBORG_SLEEP_ORCHESTRA"
LLM_KEYS_ENV = "KIBORG_LLM_KEYS"
ASK_LLM_JS_ENV = "KIBORG_ASK_LLM_JS"
ASK_LLM_TIMEOUT_MS_ENV = "KIBORG_ASK_LLM_TIMEOUT_MS"
ORCHESTRA_PY_ENV = "KIBORG_ORCHESTRA_PY"
NODE_EXE_ENV = "KIBORG_NODE_EXE"
NATIVE_LLM_TIMEOUT_MS_ENV = "KIBORG_NATIVE_LLM_TIMEOUT_MS"
ZAI_URL_ENV = "KIBORG_ZAI_URL"
ZAI_MODEL_ENV = "KIBORG_ZAI_MODEL"
ZAI_TIMEOUT_MS_ENV = "KIBORG_ZAI_TIMEOUT_MS"
ALERT_TOKEN_ENV = "KIBORG_ALERT_TOKEN"
ALERT_CHAT_ENV = "KIBORG_ALERT_CHAT_ID"
NOTIFY_TOKEN_ENV = "KIBORG_NOTIFY_TOKEN"
NOTIFY_CHAT_ENV = "KIBORG_NOTIFY_CHAT_ID"
COUNCIL_DEADLINE_ENV = "KIBORG_COUNCIL_DEADLINE"

# === LLM / AI ===============================================================
# Дефолтный таймаут LLM-вызовов (мс). Используется в ask_llm, native_llm, zai_ask.
DEFAULT_LLM_TIMEOUT_MS = 120000

# === АЛЕРТИНГ (опциональный, через Telegram Bot API) ===
# Если при прогоне случился семантический сбой (out['brain_down'] / много dropped_stub),
# harvest_log._log зовёт alerts.maybe_alert(level, msg). Когда в окружении заданы ОБА ENV —
# алерт уходит в Telegram (urllib, без новой зависимости). Нет ENV — логируется в stdout с
# пометкой [ALERT]. Токен бота храним в ENV запуска (не в llm_keys.env — это не LLM-ключ).
# Задать: export KIBORG_ALERT_TOKEN=123:abc  export KIBORG_ALERT_CHAT_ID=987654321
# Базовый URL Bot API Telegram. Может понадобиться прокси/туннель в корп.сетях.
TELEGRAM_BOT_API_BASE = "https://api.telegram.org"

# === ВРЕМЕННЫЕ ФОРМАТЫ ------------------------------------------------------
# Единый формат меток времени в логах/статусе (runs.md, source_status, rejected, triage_store).
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"

# === HTTP-константы --------------------------------------------------------
# OpenAI-совместимые провайдеры ждут application/json + Bearer; z.ai — application/json + x-api-key.
HTTP_HEADER_CONTENT_TYPE = "Content-Type"
HTTP_HEADER_AUTHORIZATION = "Authorization"
HTTP_HEADER_CONTENT_LENGTH = "Content-Length"
HTTP_HEADER_CACHE_CONTROL = "Cache-Control"
HTTP_MEDIA_TYPE_JSON = "application/json"
HTTP_CHARSET_UTF8 = "utf-8"
HTTP_MEDIA_TYPE_JSON_UTF8 = f"{HTTP_MEDIA_TYPE_JSON}; charset={HTTP_CHARSET_UTF8}"
HTTP_MEDIA_TYPE_TEXT_PLAIN_UTF8 = f"text/plain; charset={HTTP_CHARSET_UTF8}"
HTTP_MEDIA_TYPE_TEXT_HTML_UTF8 = f"text/html; charset={HTTP_CHARSET_UTF8}"
HTTP_MEDIA_TYPE_TEXT_JAVASCRIPT_UTF8 = f"text/javascript; charset={HTTP_CHARSET_UTF8}"
HTTP_MEDIA_TYPE_OCTET_STREAM = "application/octet-stream"
HTTP_CACHE_CONTROL_NO_STORE = "no-store"

# --- LLM-провайдеры (OpenAI-совместимые endpoints) ---------------------------
# Единый источник endpoint/model для провайдеров, чтобы не дублировать литералы
# в keychain.py и native_llm.py.
LLM_PROVIDER_MISTRAL = ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/chat/completions", "mistral-small-latest")
LLM_PROVIDER_OPENROUTER = ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions", "openrouter/free")
LLM_PROVIDER_GROQ = ("GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", "qwen/qwen3-32b")
LLM_PROVIDER_SAMBANOVA = ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1/chat/completions", "DeepSeek-V3.2")
LLM_PROVIDER_COHERE = ("COHERE_API_KEY", "https://api.cohere.ai/compatibility/v1/chat/completions", "command-a-03-2025")
LLM_PROVIDER_NVIDIA = (
    "NVIDIA_API_KEY",
    "https://integrate.api.nvidia.com/v1/chat/completions",
    "meta/llama-3.1-8b-instruct",
)
LLM_PROVIDER_CEREBRAS = ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions", "llama-3.3-70b")
# Closerouter — прокси-агрегатор для интуиции (muse-spark / deepseek / nemotron).
# URL вынесен сюда, чтобы не дублировать в keychain.py.
CLOSEROUTER_API_BASE = "https://api.closerouter.dev/v1/chat/completions"
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
PANEL_PORT = 8737  # локальный HTTP пульт, слушает ТОЛЬКО loopback
PANEL_HOST = "127.0.0.1"  # loopback only — панель не должна быть доступна извне
# Допустимое имя хоста в Origin для CSRF-защиты пульта (браузеры могут шить localhost вместо 127.0.0.1).
PANEL_LOCALHOST_ALIAS = "localhost"
RUN_TIMEOUT_SEC = 1200  # watchdog на один прогон (сек) — снимает зависший subprocess
# Рубильник авто-режима пульта (JSON с интервалом/last-run). Патчится в тестах: `serve.AUTO_FILE = tmp`.
AUTO_JSON = os.path.join(PANEL_DIR, "auto.json")
# Границы интервала авто-сбора (минуты). Пульт обрезает пользовательское значение в этот диапазон.
AUTO_INTERVAL_MIN_MINUTES = 5
AUTO_INTERVAL_MAX_MINUTES = 240
# Дефолтный интервал авто-сбора (минуты), если файл настроек отсутствует или повреждён.
AUTO_INTERVAL_DEFAULT_MINUTES = 30
# Период sleep внутри фонового цикла авто-режима (сек). Меньше интервала — тик проверяет чаще,
# но не запускает прогон чаще заданного interval_min.
AUTO_LOOP_SLEEP_SECONDS = 30
# Окно lock-monitor в /api/health (минуты). Сколько времени считаем "недавними" таймауты state_lock.
PANEL_HEALTH_LOCK_WINDOW_MINUTES = 60
# === SEEN ITEMS (дедуп сырых заголовков) ===
# Время жизни записи "source:id" → unix-ts (дней). Старше — выкидывается при mark_seen/_save.
SEEN_ITEMS_TTL_DAYS = 90
# Жёсткий потолок числа записей. Если TTL не справится с массовым притоком, обрезаем по свежим.
SEEN_ITEMS_MAX_RECORDS = 5000

# === ITEMS CACHE (A6 фильтр повторов автосбора по title) ===
# Храним последние N прогонов. 4-й вытолкнет самый старый.
ITEMS_CACHE_MAX_RUNS = 3
# TTL записи (сек). Даже в пределах MAX_RUNS старше TTL считается протухшей.
ITEMS_CACHE_TTL_SEC = 30 * 60  # 30 минут — типичный интервал автосбора

# === ПРОИСХОЖДЕНИЕ ИДЕЙ (wiring_ideate) ===
# Порог Jaccard для приписывания источника idea → item. Ниже — идея считается синтезированной,
# а не прямо вытекающей из заголовка источника.
PROVENANCE_JACCARD_THRESHOLD = 0.2

# === СОВЕТ НА ОТБОРЕ ИДЕЙ (wiring_council) ===
# Вес anti-bland: итоговый балл = AVG_WEIGHT × weighted_avg + MAX_WEIGHT × max(advisor scores).
# Балл важнее, но max-компонент спасает поляризующие идеи.
COUNCIL_AVG_WEIGHT = 0.7
COUNCIL_MAX_WEIGHT = 0.3
# MMR lambda: релевантность = λ × score − (1−λ) × max_sim_to_selected.
# Балл важнее разнообразия, но diversity имеет голос.
COUNCIL_MMR_LAMBDA = 0.7
# Размер топа для оценки согласия советников внутри breakdown (overlap rank_ideas×ask_llm).
COUNCIL_AGREEMENT_TOP_K = 3
# Температура для оценки читаемости readability_gate (score_llm) — детерминированный суд.
READABILITY_SCORE_TEMPERATURE = 0.2

# === НАБЛЮДАТЕЛЬ (observe_sources) ===
# Пауза между постами (сек) — в пульте строки шли живым потоком, не пачкой.
OBSERVE_ITEM_PAUSE = 0.28
# Пауза между шагами/источниками (сек).
OBSERVE_STEP_PAUSE = 0.35
# n и timeout для collect_source при наблюдении (легковесный обход).
OBSERVE_SOURCE_N = 6
OBSERVE_SOURCE_TIMEOUT = 7

# === KEYCHAIN / COUNCIL REVIEWER ===
# Сокет-таймаут одного OpenAI-совместимого вызова (сек). Эндпоинт, что вообще молчит, падает тут.
# Slow-loris добивается отдельным wall-clock deadline в make_council_chat.
KEYCHAIN_OPENAI_CHAT_TIMEOUT = 40
# Потолок токенов ответа модели в payload OpenAI-совместимых вызовах (council reviewer).
KEYCHAIN_OPENAI_MAX_TOKENS = 1024
# Температура OpenAI-совместимых вызовов (council reviewer).
KEYCHAIN_OPENAI_TEMPERATURE = 0.3

# === ИНТУИЦИЯ / ГЕНЕРАЦИЯ ИДЕЙ (ask_llm / native_llm / zai_ask) ===
# Дефолтная температура для интуиции (z.ai, нативные, closerouter-цепочка).
INTUITION_TEMPERATURE = 0.9
# Дефолтный потолок токенов ответа для интуиции.
INTUITION_MAX_TOKENS = 8192

# === СОВЕТНИК ask_llm (advisors.py) ===
# Потолок токенов ответа для ask_llm-советника ( scoring вариантов). None = без потолка.
ASK_LLM_ADVISOR_MAX_TOKENS = 256
# Температура для ask_llm-советника ( scoring вариантов).
ASK_LLM_ADVISOR_TEMPERATURE = 0.2
# Порог разброса топ-2 баллов: меньше — интуиция не уверена, поднимает флаг эскалации.
ASK_LLM_ESCALATE_GAP = 0.15
# Минимальный per-provider таймаут (мс) и запас на весь subprocess (сек) в ask_llm-органе.
ASK_LLM_MIN_PER_PROVIDER_MS = 3000
ASK_LLM_SUBPROCESS_TIMEOUT_PAD_SEC = 5

# === РЕДАКТОР ЧИТАЕМОСТИ (wiring_council) ===
# Температура для детерминированного судейства читаемости (score_llm) — должен парситься в JSON.
READABILITY_SCORE_TEMPERATURE = 0.2

# === НАТИВНЫЕ LLM (native_llm.py) ===
# Минимальный per-provider таймаут (мс) при обходе нативных провайдеров.
NATIVE_LLM_MIN_PER_PROVIDER_MS = 5000

# === АВТОСБОР (harvest_runner.py) ===
# Максимальное число прогонов за один вызов harvest_runner.main (предохранитель).
HARVEST_RUNNER_MAX_RUNS = 50

# === LOCK-MONITOR (lock_monitor.py) ===
# Окно health-пульта: сколько минут назад считать зафиксированные таймауты state_lock.
LOCK_MONITOR_RECENT_TIMEOUTS_MINUTES = 60

# === NOTIFY (notify.py) ===
# Сколько заголовков идей показывать в Telegram-уведомлении (до «и ещё N»).
NOTIFY_MAX_TITLES = 10

# === ORCHESTRA-СОВЕТНИК (advisors.py) ===
# Дефолтный таймаут одного варианта при orchestra review (сек).
ORCHESTRA_TIMEOUT_SEC = 180

# === WIRING_COUNCIL (wiring_council.py) ===
# Дефолтный таймаут одной идеи при orchestra-голосовании в council (сек).
WIRING_COUNCIL_ORCHESTRA_TIMEOUT_SEC = 45

# === HARVEST_LOG (harvest_log.py) ===
# Сколько символов результата писать в строку runs.md (обрезка для читаемости).
HARVEST_LOG_RESULT_MAX_CHARS = 120

# === HARVEST_RUNNER (harvest_runner.py) ===
# Сколько символов ошибки печатать в консоль при best-effort пропуске feedback_cortex.
HARVEST_RUNNER_ERROR_MAX_CHARS = 160

# === PROVENANCE (wiring_ideate.py) ===
# Сколько символов context item'а учитывать при Jaccard-подборе источника идеи.
PROVENANCE_CONTEXT_MAX_CHARS = 500

# === RANK_IDEAS (advisors.py) ===
# Сколько source_refs показывать в тексте варианта для rank_ideas-арбитра.
RANK_IDEAS_MAX_REFS = 3

# === TELEGRAM-ФЕТЧ (collect_tg_news) ===
# Таймаут фетча телеграм-каналов (сек). 21 канал × 5 постов — глубже фетч, шире таймаут.
TELEGRAM_FETCH_TIMEOUT = 90

# === ПОТОЛКИ ПАПОК-ИСТОЧНИКОВ (folders) ===
# Максимальное число папок-источников. Больше — мусор/раздувание списка.
MAX_FOLDERS = 40
# Максимальная длина одного пути папки (символов). Длиннее — обрезается.
MAX_FOLDER_PATH_LEN = 400
# === РУЛЬ НАПРАВЛЕНИЯ (direction) ===
# Дефолтный список пресетов тем, когда файла настроек ещё нет.
DEFAULT_DIRECTION_PRESETS = ["дев-тулзы", "железки", "для родителей", "игры", "здоровье", "бизнес"]
# Максимальная длина строки темы (символов). Длиннее — обрезается.
MAX_DIRECTION_THEME_LEN = 120
# Максимальное число пресетов. Больше — мусор/раздувание списка.
MAX_DIRECTION_PRESETS = 40
# Feature-lab статус фич (внутренний). Патчится в тестах: `serve.LAB_ROUTER = tmp`.
LAB_ROUTER_FILE = os.path.join(PROJECT_ROOT, ".feature-lab", "router.json")
