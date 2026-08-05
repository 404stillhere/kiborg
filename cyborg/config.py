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
RUNS_MD_FILE = "runs.md"
RUNS_MD = os.path.join(CYBORG_DATA_DIR, RUNS_MD_FILE)
# Живой per-source статус для пульта — пишет harvest_gate._persist_status, читает serve._read_source_status.
SOURCE_STATUS_FILE_NAME = "source_status.json"
SOURCE_STATUS_FILE = os.path.join(CYBORG_DATA_DIR, SOURCE_STATUS_FILE_NAME)
# Gate-отпечаток ленты («есть что новое?») — пишут/читают harvest_gate._save_sig/_last_sig.
# Патчится в тестах: `harvest.STATE_FILE = tmp` (фасадный алиас).
HARVEST_STATE_FILE_NAME = "harvest_state.json"
HARVEST_STATE_FILE = os.path.join(CYBORG_DATA_DIR, HARVEST_STATE_FILE_NAME)
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
IE_STATE_FILE = "state.json"
INBOX_MD_FILE = "inbox.md"
INBOX_MD = os.path.join(IDEA_ENGINE_DATA_DIR, INBOX_MD_FILE)  # инбокс идей для человека
IE_STATE_JSON = os.path.join(IDEA_ENGINE_DATA_DIR, IE_STATE_FILE)  # Store idea_engine (счётчик открытых)
IE_NOTIFY_MD_FILE = "notify.md"
IE_NOTIFY_MD = os.path.join(IDEA_ENGINE_DATA_DIR, IE_NOTIFY_MD_FILE)  # файловое уведомление о новых идеях
# Каталог Oracle-планов (внутри idea_engine/data), пишет deliver_oracle, читает panel/serve._read_oracles.
ORACLES_DIR = os.path.join(IDEA_ENGINE_DATA_DIR, "oracles")

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
# Центр шкалы advisor_score: (score − 0.5) × 2 переводит [0,1] в [-1,1].
FEEDBACK_CORTEX_SCORE_CENTER = 0.5
FEEDBACK_CORTEX_SCORE_SCALE = 2.0
# Общие числовые константы (чтобы не дублировать литералы в feedback_cortex).
ZERO_WEIGHT = 0.0
UNIT_WEIGHT = 1.0

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
# Границы шкалы score идей (0..10).
SCORE_MIN = 0.0
SCORE_MAX = 10.0

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
# Период poll при захвате tg-сессии state_lock (сек). Меньше — быстрее реакция, больше — меньше CPU.
TG_LOCK_POLL_INTERVAL = 0.2
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
# Дефолтный исполняемый файл Node.js (ищется в PATH).
DEFAULT_NODE_EXE = "node"
NATIVE_LLM_TIMEOUT_MS_ENV = "KIBORG_NATIVE_LLM_TIMEOUT_MS"
ZAI_URL_ENV = "KIBORG_ZAI_URL"
ZAI_MODEL_ENV = "KIBORG_ZAI_MODEL"
ZAI_TIMEOUT_MS_ENV = "KIBORG_ZAI_TIMEOUT_MS"
# Дефолтный URL и модель z.ai (переопределяются через env).
ZAI_DEFAULT_URL = "https://api.z.ai/api/anthropic/v1/messages"
ZAI_DEFAULT_MODEL = "glm-5.2"
ALERT_TOKEN_ENV = "KIBORG_ALERT_TOKEN"
ALERT_CHAT_ENV = "KIBORG_ALERT_CHAT_ID"
NOTIFY_TOKEN_ENV = "KIBORG_NOTIFY_TOKEN"
NOTIFY_CHAT_ENV = "KIBORG_NOTIFY_CHAT_ID"
COUNCIL_DEADLINE_ENV = "KIBORG_COUNCIL_DEADLINE"
# Дефолтный wall-clock потолок на один вызов рецензента (keychain._with_deadline), сек.
COUNCIL_DEADLINE_DEFAULT_SEC = 50

# === LLM / AI ===============================================================
# Дефолтный таймаут LLM-вызовов (мс). Используется в ask_llm, native_llm, zai_ask.
DEFAULT_LLM_TIMEOUT_MS = 120000
# Таймаут ask_llm-советника при обходе цепочки провайдеров (мс).
ASK_LLM_ADVISOR_TIMEOUT_MS = 60000
# Таймаут нативных LLM-провайдеров на одного провайдера (мс).
NATIVE_LLM_PROVIDER_TIMEOUT_MS = 5000

# === АЛЕРТИНГ (опциональный, через Telegram Bot API) ===
# Если при прогоне случился семантический сбой (out['brain_down'] / много dropped_stub),
# harvest_log._log зовёт alerts.maybe_alert(level, msg). Когда в окружении заданы ОБА ENV —
# алерт уходит в Telegram (urllib, без новой зависимости). Нет ENV — логируется в stdout с
# пометкой [ALERT]. Токен бота храним в ENV запуска (не в llm_keys.env — это не LLM-ключ).
# Задать: export KIBORG_ALERT_TOKEN=123:abc  export KIBORG_ALERT_CHAT_ID=987654321
# Базовый URL Bot API Telegram. Может понадобиться прокси/туннель в корп.сетях.
TELEGRAM_BOT_API_BASE = "https://api.telegram.org"
# Шаблон публичной ссылки на Telegram-пост.
TELEGRAM_POST_URL_TEMPLATE = "https://t.me/{username}/{msg_id}"
# Паттерны секретных URL для scrub_secrets (орган vendored, копия в kiborg).
SCRUB_SLACK_WEBHOOK_URL = r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]{20,}"
SCRUB_DISCORD_WEBHOOK_URL = r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_\-]{20,}"

# === ВРЕМЕННЫЕ ФОРМАТЫ ------------------------------------------------------
# Единый формат меток времени в логах/статусе (runs.md, source_status, rejected, triage_store).
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
# Формат таймстемпа для каталогов бэкапов / pre-restore копий / corrupted-дампов.
# Лексикографическая сортировка = хронологическая — удобно для ротации и глаз.
BACKUP_TS_FMT = "%Y-%m-%d_%H%M%S"
# Форматы Oracle-планов (idea_engine/organs/deliver_oracle.py).
# Имя файла: <date>_<time>.md; UI-пульта: date + time без секунд; индекс: полная метка.
ORACLE_PLAN_DATE_FMT = "%Y-%m-%d"
ORACLE_PLAN_TIME_FMT = "%H-%M-%S"
ORACLE_PLAN_INDEX_FMT = "%Y-%m-%d %H:%M"
# ISO-формат timestamp'а без дробных секунд (triage_events, shadow_metrics).
ISO_TIMESTAMP_SECONDS_FMT = "seconds"
# Часы пульта (/api/state now) — только время, секунды нужны для live-ощущения.
PANEL_CLOCK_FMT = "%H:%M:%S"

# === HTTP-константы --------------------------------------------------------
# OpenAI-совместимые провайдеры ждут application/json + Bearer; z.ai — application/json + x-api-key.
PYTHONIOENCODING_UTF8 = "utf-8"  # env-переменная для child-процессов, чтобы stdin/stdout были UTF-8
HTTP_HEADER_CONTENT_TYPE = "Content-Type"
HTTP_HEADER_AUTHORIZATION = "Authorization"
HTTP_HEADER_AUTHORIZATION_BEARER_PREFIX = "Bearer "
HTTP_HEADER_CONTENT_LENGTH = "Content-Length"
HTTP_HEADER_CACHE_CONTROL = "Cache-Control"
HTTP_HEADER_ORIGIN = "Origin"
# z.ai (Anthropic Messages API) — ключ через заголовок x-api-key + версия API.
HTTP_HEADER_X_API_KEY = "x-api-key"
HTTP_HEADER_ANTHROPIC_VERSION = "anthropic-version"
HTTP_ANTHROPIC_VERSION_DATE = "2023-06-01"
HTTP_MEDIA_TYPE_JSON = "application/json"
HTTP_CHARSET_UTF8 = "utf-8"
HTTP_DECODE_ERRORS_REPLACE = "replace"
HTTP_DECODE_ERRORS_SURROGATEESCAPE = "surrogateescape"
# Fallback-кодировки для декодирования чужих файлов (collect_source._files_decode).
FILES_DECODE_ENCODING_LATIN1 = "latin-1"
FILES_DECODE_ENCODING_CP1251 = "cp1251"
HTTP_MEDIA_TYPE_JSON_UTF8 = f"{HTTP_MEDIA_TYPE_JSON}; charset={HTTP_CHARSET_UTF8}"
HTTP_MEDIA_TYPE_TEXT_PLAIN_UTF8 = f"text/plain; charset={HTTP_CHARSET_UTF8}"
HTTP_MEDIA_TYPE_TEXT_HTML_UTF8 = f"text/html; charset={HTTP_CHARSET_UTF8}"
HTTP_MEDIA_TYPE_TEXT_JAVASCRIPT_UTF8 = f"text/javascript; charset={HTTP_CHARSET_UTF8}"
# Расширения/файлы, которые oracle_scan считает точками входа в проекте.
ORACLE_ENTRYPOINT_README = "README.md"
ORACLE_ENTRYPOINT_MAIN = "main.py"
HTTP_MEDIA_TYPE_OCTET_STREAM = "application/octet-stream"
HTTP_MEDIA_TYPE_X_WWW_FORM_URLENCODED = "application/x-www-form-urlencoded"
HTTP_CACHE_CONTROL_NO_STORE = "no-store"
HTTP_METHOD_POST = "POST"
# User-Agent для HTTP-источников idea_engine (collect_source), чтобы не дублировать строку
# в замороженном органе. Имя заголовка вынесено отдельно, чтобы формировать dict headers
# через константу.
HTTP_HEADER_USER_AGENT = "User-Agent"
HTTP_USER_AGENT = "kiborg-idea-engine/1.0 (personal script, non-commercial)"
HTTP_USER_AGENT_MOZILLA_PREFIX = "Mozilla/5.0 ("
HTTP_USER_AGENT_MOZILLA_SUFFIX = ")"
# URL-префиксы источников (idea_engine/collect_source), чтобы не дублировать литералы.
REDDIT_URL_PREFIX = "https://reddit.com"
GITHUB_URL_PREFIX = "https://github.com"
# Суффикс pyrogram-файла сессии; collect_tg_news нормализует путь, убирая его.
TELEGRAM_SESSION_SUFFIX = ".session"

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
# Gemini — отключён геоблоком, но спека остаётся (можно вернуть, убрав из _COUNCIL_DISABLED).
LLM_PROVIDER_GEMINI = (
    "GEMINI_API_KEY",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "gemini-2.5-flash",
)
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
PANEL_HOST_PORT_TEMPLATE = "{host}:{port}"  # для HTTP-заголовка Host
PANEL_HTTP_SCHEME = "http"
PANEL_URL_TEMPLATE = "{scheme}://{host}:{port}"  # базовый URL пульта
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
# Дублирует LOCK_MONITOR_RECENT_TIMEOUTS_MINUTES — оставлено отдельно, т.к. патчится в тестах независимо.
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
# Порог согласия rank_ideas×ask_llm для lazy_orchestra: Jaccard overlap топ-K должен быть
# НЕ меньше этого значения, иначе оркестр подключается для разрешения расхождения.
COUNCIL_LAZY_ORCHESTRA_AGREEMENT_THRESHOLD = 2 / 3
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
# Ширина рамки-заголовка в консоли наблюдателя (символов «=»).
OBSERVE_FRAME_WIDTH = 60

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
# Секунд в минуте — для перевода минутных констант в секунды.
SECONDS_PER_MINUTE = 60

# === NOTIFY (notify.py) ===
# Сколько заголовков идей показывать в Telegram-уведомлении (до «и ещё N»).
NOTIFY_MAX_TITLES = 10

# === ORCHESTRA-СОВЕТНИК (advisors.py) ===
# Дефолтный таймаут одного варианта при orchestra review (сек).
ORCHESTRA_TIMEOUT_SEC = 180

# === WIRING_COUNCIL (wiring_council.py) ===
# Дефолтный таймаут одной идеи при orchestra-голосовании в council (сек).
WIRING_COUNCIL_ORCHESTRA_TIMEOUT_SEC = 45
# Таймаут на всю цепочку провайдеров интуиции ask_llm в council (мс).
WIRING_COUNCIL_LLM_TIMEOUT_MS = 45000

# === HARVEST_LOG (harvest_log.py) ===
# Сколько символов результата писать в строку runs.md (обрезка для читаемости).
HARVEST_LOG_RESULT_MAX_CHARS = 120

# === HARVEST_RUNNER (harvest_runner.py) ===
# Сколько символов ошибки печатать в консоль при best-effort пропуске feedback_cortex.
HARVEST_RUNNER_ERROR_MAX_CHARS = 160

# === ORCHESTRATOR (orchestrator.py / run.py / harvest_runner.py) ===
# Сколько шагов максимально делает цикл оркестратора (предохранитель от бесконечного спина).
CYBORG_MAX_STEPS = 8
# Сколько органов отбирает роутер для мозга по умолчанию.
CYBORG_ROUTE_K = 5
# Сколько органов отбирает роутер в живом прогоне, чтобы точно влезла вся цепочка
# (collect -> ideate -> rank -> readability -> scrub -> deliver = 6+) + finish.
CYBORG_ROUTE_K_FULL_CHAIN = 6

# === ASK_LLM (ask_llm.py) ===
# Сколько символов smoke-ответа печатать в консоль при диагностике.
ASK_LLM_SMOKE_MAX_CHARS = 160

# === NATIVE_LLM (native_llm.py) ===
# Сколько символов smoke-ответа печатать в консоль при диагностике.
NATIVE_LLM_SMOKE_MAX_CHARS = 160

# === OBSERVE_SOURCES (observe_sources.py) ===
# Сколько символов исключения печатать при сбое источника.
OBSERVE_ERROR_MAX_CHARS = 80
# Сколько символов degraded_reason печатать в консоль.
OBSERVE_DEGRADED_MAX_CHARS = 80
# Сколько символов заголовка item'а печатать в консоль (после scrub_secrets).
OBSERVE_TITLE_MAX_CHARS = 72

# === MIND (mind.py) ===
# Сколько символов исключения советника показывать в reason при краше.
MIND_EXCEPTION_MAX_CHARS = 80

# === RUN (run.py) ===
# Сколько символов ошибки загрузки каталога печатать в режиме ручного прогона.
RUN_CATALOG_ERROR_MAX_CHARS = 30
# Сколько символов результата печатать в консоль в режиме ручного прогона.
RUN_RESULT_MAX_CHARS = 900

# === PROVENANCE (wiring_ideate.py) ===
# Сколько символов context item'а учитывать при Jaccard-подборе источника идеи.
PROVENANCE_CONTEXT_MAX_CHARS = 500
# Сколько declared source_ids рассматривать при прямом связывании idea → item.
PROVENANCE_MAX_DECLARED_SOURCE_IDS = 4

# === BRAIN (brain.py) ===
# Сколько символов purpose органа показывать LLM-планировщику.
BRAIN_PURPOSE_MAX_CHARS = 80

# === RANK_IDEAS / ADVISORS (advisors.py + idea_engine/organs/rank_ideas.py) ===
# Сколько source_refs показывать в тексте варианта для rank_ideas-арбитра.
RANK_IDEAS_MAX_REFS = 3
# Сколько символов why/reason в тексте варианта для rank_ideas-арбитра.
RANK_IDEAS_WHY_MAX_CHARS = 260
# Сколько символов verification в тексте варианта для rank_ideas-арбитра.
RANK_IDEAS_VERIFICATION_MAX_CHARS = 180
# Сколько символов title/path/id одного source_ref в тексте варианта.
RANK_IDEAS_REF_TITLE_MAX_CHARS = 100

# === WIRING_SCRUB (wiring_scrub.py) ===
# Сколько source_refs сканируем на утечку секретов при финальной чистке идей.
WIRING_SCRUB_MAX_REFS = 4

# === STRESS-ТЕСТ (stress/stress_test_harvest.py) ===
# Дефолтное число прогонов stress-теста (аргумент по умолчанию и верхняя граница clamp'а).
STRESS_DEFAULT_ITERATIONS = 50
STRESS_MAX_ITERATIONS = 1000
# Префикс временной директории, куда редиректятся state/data/runs.
STRESS_TMPDIR_PREFIX = "stress_"
# Источник и заголовки фейковых items в моке collect_source.run.
STRESS_FAKE_SOURCE = "stress"
STRESS_FAKE_TITLE_TEMPLATE = "stub-idea-{i}"
STRESS_FAKE_URL_TEMPLATE = "http://example.com/{i}"

# === TEST RUNNER (run_tests.py) ===
# Пакеты, которые раннер прогоняет в раздельных subprocess'ах.
TEST_RUNNER_PACKAGES = ["cyborg", "idea_engine", "panel"]
# Regex-паттерны для парсинга сводки pytest -q.
TEST_RUNNER_PASSED_RE = r"(\d+) passed"
TEST_RUNNER_FAILED_RE = r"(\d+) failed"
TEST_RUNNER_ERROR_RE = r"(\d+) error"
# Ключи результирующего dict (используются и как имена полей, и как строки в выводе).
TEST_RUNNER_STATUS_OK = "OK"
TEST_RUNNER_STATUS_FAIL = "FAIL"
TEST_RUNNER_STATUS_NORUN = "NORUN"
TEST_RUNNER_VERDICT_ALL_GOOD = "ВСЕ ЗЕЛЁНЫЕ"
TEST_RUNNER_VERDICT_PROBLEMS = "ЕСТЬ ПРОБЛЕМЫ (падения / pytest не выполнился)"
# Сколько строк хвоста pytest печатать при аномалии.
TEST_RUNNER_TAIL_LINES = 15

# === TELEGRAM-ФЕТЧ (collect_tg_news) ===
# Таймаут фетча телеграм-каналов (сек). 21 канал × 5 постов — глубже фетч, шире таймаут.
TELEGRAM_FETCH_TIMEOUT = 90
# Потолок сообщений на один ТГ-канал по умолчанию.
TELEGRAM_LIMIT_PER_CHANNEL = 50

# === IDEA_ENGINE RUN (idea_engine/run.py) ===
# Параметры legacy standalone-tick (режим без cyborg/orchestrator). Живой конвейер берёт
# n из harvest.SOURCE_N, а здесь — дефолт для ручного `python idea_engine/run.py tick`.
LEGACY_TICK_SOURCE_N = 8
LEGACY_TICK_SOURCE = "hn"
LEGACY_TICK_K = 3
LEGACY_TICK_CAP = 0  # 0 = без потолка

# === ПАНЕЛЬНЫЕ JSON-КОНФИГИ (cyborg/*.py) ===
# Имена файлов-конфигов пульта (в cyborg/data/). Ссылки через _panel_config.data_dir_for.
COUNCIL_CONFIG_FILE = "council.json"
COUNCIL_WEIGHTS_FILE = "council_weights.json"
DIRECTION_FILE = "direction.json"
FEEDS_FILE = "feeds.json"
FOLDERS_FILE = "folders.json"
GENPARAMS_FILE = "genparams.json"
ITEMS_CACHE_FILE = "items_cache.json"
SEEN_ITEMS_FILE = "seen_items.json"

# === TRIAGE_STORE (idea_engine/triage_store.py) ===
# Имена master-файлов разобранных идей (take / later).
TAKEN_FILE = "taken.json"
LATER_FILE = "later.json"
# Ключи внутри taken.json / later.json.
TRIAGE_STORE_TAKEN_KEY = "taken"
TRIAGE_STORE_LATER_KEY = "later"
TRIAGE_STORE_KEYS = (TRIAGE_STORE_TAKEN_KEY, TRIAGE_STORE_LATER_KEY)
# Префикс имени файла для определения ключа: taken* → taken, иначе later.
TRIAGE_STORE_TAKEN_PREFIX = "taken"
# Суффикс атомарного tmp-файла: pid уникализирует имя при параллельной записи.
ATOMIC_TMP_PID_SUFFIX = "{pid}.tmp"

# === REJECTED / TRIAGE_EVENTS (idea_engine/rejected.py, triage_events.py) ===
# Мастер-файл отклонённых идей (title+why) — учит генератор/судью.
REJECTED_FILE = "rejected.json"
# Ключ внутри rejected.json.
REJECTED_KEY = "rejected"
# Append-only журнал действий триажа (сигнал Feedback Cortex).
TRIAGE_EVENTS_FILE_NAME = "triage_events.jsonl"
# Дубль для модулей cyborg/, которым удобнее читать как «файл в data/» (feedback_cortex).
TRIAGE_EVENTS_FILE = TRIAGE_EVENTS_FILE_NAME
REJECTED_MAX_ITEMS = 200
# Сколько последних подавать генератору/судье как «не повторяй» (промпт не раздуть).
REJECTED_CONTEXT_N = 25
# Обрезка поля title отклонённой идеи.
REJECTED_TITLE_MAX_CHARS = 300
# Обрезка поля why отклонённой идеи.
REJECTED_WHY_MAX_CHARS = 400

# === СБОР ВНЕШНИХ ИСТОЧНИКОВ (idea_engine/organs/collect_source.py) ===
# URL-адреса публичных API источников (HN/Reddit/Lobsters/GitHub Trending).
HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_SHOW_URL = "https://hacker-news.firebaseio.com/v0/showstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
REDDIT_TOP_URL = "https://www.reddit.com/r/SideProject/top.json?t=day&limit={}"
LOBSTERS_HOT_URL = "https://lobste.rs/hottest.json"
GH_TRENDING_URL = "https://github.com/trending"
GH_REPO_API_URL = "https://api.github.com/repos/{owner}/{repo}"
# GitHub Trending: сколько репо обогащать description через API (лимит 60/час для IP без токена).
GH_TRENDING_ENRICH_LIMIT = 5
# Таймаут git ls-files внутри _files_git_walk (сек).
FILES_GIT_LSFILES_TIMEOUT = 8
# Максимальный размер файла-папки-источника (байт), который читаем.
FILES_MAX_BYTES = 1024 * 1024
# Сколько байт читаем с начала файла для headline (переживает лицензионную/сгенерированную шапку).
FILES_HEAD_BYTES = 32 * 1024
# Максимальное число символов контекста одного файла в промпт.
FILES_CONTEXT_CHARS = 1600
# Максимальное число строк контекста одного файла.
FILES_CONTEXT_LINES = 20
# Максимальное число файловых item'ов за прогон.
FILES_MAX_ITEMS = 48
# Предохранитель: сколько файлов максимум осматриваем за прогон.
FILES_MAX_SCAN = 20000
# Максимальное число карт проектов, которые добавляем к выдаче файлов.
FILES_MAX_PROJECT_MAPS = 8
# Сколько символов берём из description репозитория GitHub при обогащении.
GH_REPO_DESCRIPTION_MAX_CHARS = 180
# Сколько символов печатаем из stderr Telegram-RPC при ошибке.
TELEGRAM_RPC_ERROR_MAX_CHARS = 200
# Сколько символов берём из первой строки поста Telegram.
TELEGRAM_POST_TITLE_MAX_CHARS = 200
# Сколько символов строки безопасной линии файла (раскрытие табов + отступ).
FILES_SAFE_LINE_MAX_CHARS = 260
# Сколько символов составляет headline файла.
FILES_HEADLINE_MAX_CHARS = 180
# Сколько символов берём из строки контекста файла при форматировании L{lineno}: ... .
FILES_CONTEXT_LINE_MAX_CHARS = 220
# Сколько символов берём из итогового title файлового item'а.
FILES_ITEM_TITLE_MAX_CHARS = 240
# Сколько зависимостей максимум выводим в контекст файла.
FILES_DEPS_MAX_ITEMS = 10
# Вес jitter для ротирующей выборки _files_select (score + random * jitter).
FILES_SELECT_JITTER = 18.0
# Минимальный размер secret_probe (половина от двустороннего окна).
FILES_SECRET_PROBE_HALF_WINDOW = 2500
# Порог длины строки, выше которой используем двустороннее окно для секрет-скана.
FILES_SECRET_PROBE_FULL_WINDOW = 5000

# === ORACLE-SCAN (idea_engine/organs/oracle_scan.py) ===
# Максимальное число файлов в карте проекта.
ORACLE_SCAN_MAX_FILES = 2000
# Глубина дерева файлов в карте проекта.
ORACLE_SCAN_MAX_TREE_DEPTH = 4
# Максимальное число байт, читаемых из файла при подсчёте маркеров.
ORACLE_SCAN_CONTENT_MAX_BYTES = 200_000
# Сколько ошибок сохраняем в project_map.
ORACLE_SCAN_MAX_ERRORS = 20
# Сколько строк инбокса разбираем на заголовки.
ORACLE_SCAN_INBOX_LINES = 30
# Сколько недавних заголовков инбокса показываем.
ORACLE_SCAN_INBOX_RECENT = 5
# Сколько байт читаем из README для summary.
ORACLE_SCAN_README_BYTES = 2048
# Сколько содержательных строк README сохраняем.
ORACLE_SCAN_README_LINES = 15
# Сколько строк возможностей (buf) добавляем к summary, если основных мало.
ORACLE_SCAN_README_FEATURES_BUF = 5
# Максимальная длина итогового summary README (символов).
ORACLE_SCAN_README_SUMMARY_MAX_CHARS = 1200

# === ORACLE-PLAN (idea_engine/organs/oracle_plan.py) ===
# Максимальное число файлов, передаваемых в промпт планировщика.
ORACLE_PLAN_MAX_FILES = 200
# Сколько слов минимум должна содержать цель.
ORACLE_PLAN_MIN_GOAL_WORDS = 3

# === STORE / DEDUP (idea_engine/store.py) ===
# Порог Jaccard для дедупликации идей по значимым словам заголовка.
# Используется только когда ни одна из сигнатур не является подмножеством другой.
STORE_DEDUP_JACCARD_THRESHOLD = 0.6
# Дефолтный потолок открытых идей (дорожка A). 0/None = копилка без потолка.
STORE_DEFAULT_CAP = 3
# Потолок памяти предложенного: помним последние N сигнатур заголовков.
STORE_SEEN_CAP = 5000
# Валидные статусы идей. OPEN — открытая, остальные — разобранные (take/later/trash).
STORE_STATUS_OPEN = "open"
STORE_STATUS_TAKE = "take"
STORE_STATUS_LATER = "later"
STORE_STATUS_TRASH = "trash"
# Множество «разобранных» статусов (take/later/trash).
STORE_CLEARED_STATUSES = {STORE_STATUS_TAKE, STORE_STATUS_LATER, STORE_STATUS_TRASH}
# Все валидные статусы.
STORE_VALID_STATUSES = STORE_CLEARED_STATUSES | {STORE_STATUS_OPEN}

# === FINISH_STEP (idea_engine/organs/finish_step.py) ===
# Сколько символов next_step брежем в nudge.
FINISH_STEP_WHY_MAX_CHARS = 220

# === IDEATE (idea_engine/organs/ideate.py) ===
# Сколько символов title источника берём в stub-идею.
IDEATE_STUB_TITLE_MAX_CHARS = 60
# Сколько source_ids сохраняем в карточке идеи.
IDEATE_MAX_SOURCE_IDS = 4
# Сколько символов source_id сохраняем.
IDEATE_SOURCE_ID_MAX_CHARS = 120
# Максимальная длина verification в карточке идеи.
IDEATE_VERIFICATION_MAX_CHARS = 500

# === DELIVER_ORACLE (idea_engine/organs/deliver_oracle.py) ===
# План с той же целью/проектом, созданный не позднее этого окна, считается дубликатом (часы).
ORACLE_DEDUP_WINDOW_HOURS = 24

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

# === ФАЙЛОВЫЕ СУФФИКСЫ / ИМЕНА (живой код + тесты патчат пути, но строковые суффиксы
# исторически дублировались в 4+ модулях — собраны здесь как единый источник истины) ===
# Суффикс для атомарной записи (tmp + os.replace — обрыв записи не бьёт существующий файл).
# Используется в harvest_gate._atomic_write, items_cache._atomic_write, _panel_config,
# harvest_log._rotate_if_needed, ask_llm._save_provider, seen_items._save.
ATOMIC_TMP_SUFFIX = ".tmp"
# Суффикс lock-файла вокруг tg-сессии (wiring_collect._remove_stale_lock + frozen store.state_lock).
TG_LOCK_SUFFIX = ".lock"
# Префикс страховочной копии перед restore_backup.restore() — на случай, если восстановили не то.
PRE_RESTORE_PREFIX = ".pre-restore-"
# Префикс копии повреждённого state.json перед восстановлением (recover_state.auto_recover_*).
STATE_CORRUPTED_PREFIX = ".corrupted-"
# Журнал triage-событий, который читает feedback_cortex.main().
TRIAGE_EVENTS_FILE = "triage_events.jsonl"
# Журнал shadow-метрик lazy orchestra (наблюдатель, не меняет поведение).
SHADOW_METRICS_FILE = "shadow_metrics.jsonl"
# Расширение файлов планов Oracle и индекса.
ORACLE_PLAN_EXT = ".md"
# Имя индексного файла Oracle (внутри ORACLES_DIR).
ORACLE_INDEX_FILE = "index.md"
# Имена статики панели v1 (старый пульт, оставлен для обратной совместимости).
PANEL_V1_INDEX_FILE = "index.html"
PANEL_V1_BODIES_FILE = "bodies.js"
# Роуты статики панели v2 (новый пульт).
PANEL_V2_STATIC_ROUTES = ("/", "/index.html", "/style.css", "/app.js")
PANEL_V2_INDEX_FILE = "index.html"

# === FEEDBACK_CORTEX (feedback_cortex.py) ===
# Допуск при проверке сходимости clamp+renormalize (модуль итеративно стабилизирует веса).
FEEDBACK_CORTEX_CONVERGENCE_EPS = 1e-9

# === SEEN_ITEMS (seen_items.py) ===
# Секунд в сутках — для перевода TTL_DAYS в cutoff (ранее литерал 86400 был в одном месте).
SECONDS_PER_DAY = 86400

# === WIRING_COLLECT (wiring_collect.py) ===
# Дефолтный бюджет и лента для _run_collect, когда env не принёс (например, вызов из теста
# или прямой запуск старого сценария). Эти же значения покрывают регресс-тест 2026-07-12.
COLLECT_DEFAULT_N = 8
COLLECT_DEFAULT_SOURCE = "hn"
