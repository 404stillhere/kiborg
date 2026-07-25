"""ФАСАД автономного сбора идей — «фон» киборга (когда он гоняет сам по таймеру с пульта).

Раньше это был монолит (~432 строк): здесь жили и env-сборщики (_source_env/wire_council/
_harvest_env), и гейт «есть что нового?» (_source_signature/_should_run/...), и форматтеры
лога (council_note/_degrade_note/_log), и точка входа main. Теперь монолит разбит на 4
коротких подмодуля по одной зоне ответственности каждый:

    harvest_env.py    — _active_sources, _load_darbot_tg_creds, _source_env, wire_council,
                         _harvest_env (конфигурация источников + впайка совета)
    harvest_gate.py   — _titles_sig, _status_from_out, _atomic_write, _persist_status,
                         _source_signature, _last_sig, _should_run, _save_sig
                         (гейт «есть что нового?» — дёшево без LLM)
    harvest_log.py    — council_note, _degrade_note, _log (форматтеры + запись в runs.md)
    harvest_runner.py — main (цикл прогонов: гейт → cy.run → лог)

Этот файл (harvest.py) ОСТАЁТСЯ точкой входа для внешних потребителей (run.py,
observe_sources.py, ВСЕ тесты): сохраняет (1) sys.path-хак cyborg/ + stdout.reconfigure,
(2) импорт органов как атрибуты harvest.* (патчатся в тестах: harvest.feeds.enabled,
harvest.folders.current, harvest.direction.current, harvest._load_darbot_tg_creds,
harvest._KIBORG_TG_SESSION, harvest.STATE_FILE), (3) константы (DATA, STATE_FILE, STATUS_FILE,
SOURCE_N, TELEGRAM_CHANNELS, USER_VERIFIED_SOURCES, _DARBOT_ENV, _KIBORG_TG_SESSION,
_IE_DATA), и реэкспортит все публичные символы из подмодулей. Подмодули обращаются к
органам/константам через `import harvest; harvest.X` — так патч `harvest.X = mock` в тестах
доходит до живого кода.

ТОТ ЖЕ конвейер и ТА ЖЕ куча, что у ручной кнопки «Принеси идеи» (collect -> ideate ->
rank -> scrub -> deliver в инбокс). Разница только в поведении фона: гейт «есть что нового?»
(пустые прогоны пропускаем) + фильтр «уже видели» (не тащим одни и те же посты). Идеи
копятся в одну кучу без потолка, дедуп отсеивает повторы; разбираешь в своём темпе.

Запуск:
    python harvest.py         — один прогон
    python harvest.py 5       — 5 прогонов подряд (за один вызов набрать больше)

Каждый прогон логируется в data/runs.md (как и ручные прогоны).
"""

import os
import sys

# path-bootstrap: добавляет cyborg/ и idea_engine/ в sys.path идемпотентно.
# Вынесено в общий модуль bootstrap_paths.py — ТЕПЕРЬ `import harvest` (и `python harvest.py`)
# работают АВТОНОМНО, без предварительного `import wiring` (раньше падали на `import rejected`,
# который живёт в idea_engine/, а idea_engine/ в path клал только wiring). Подробности — в
# bootstrap_paths.py. Сначала добавляем свой каталог (чтобы `import bootstrap_paths` резолвился).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootstrap_paths  # noqa: E402

bootstrap_paths.ensure_project_paths()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Органы здесь импортируются НЕ для прямого использования в фасаде (код уехал в подмодули),
# а чтобы остаться атрибутами harvest.* — их патчат тесты (harvest.feeds.enabled,
# harvest.folders.current, harvest.direction.current, harvest._load_darbot_tg_creds) и читают
# подмодули через `import harvest`. Потому F401 на каждом. E402 — импорты после sys.path-хака.
import ask_llm  # noqa: E402,F401  (last_provider мок для provider-проброса в _run_ideate)
import direction  # noqa: E402,F401  (руль темы: env["direction"] для генератора/судьи)
import feeds  # noqa: E402,F401  (ленты-источник: какие публичные ленты включены, тумблеры в пульте)
import folders  # noqa: E402,F401  (папки-источник: env["files_paths"], список правится в пульте)
import genparams  # noqa: E402,F401  (параметры генерации: env["gen_k"]/["rank_keep"]/... для wiring; правятся в пульте)
import keychain  # noqa: E402,F401  (ключи -> совет на отборе; впаивается wire_council для ОБЕИХ кнопок)
import rejected  # noqa: E402,F401  (отклонённые: env["rejected"] — генератор/судья не приносят похожее; idea_engine на path через wiring)
import seen_items  # noqa: E402,F401

# Константы — мутабельные алиасы из единого config.py (источник истины). Имена те же, что и
# раньше, чтобы тесты (harvest.STATE_FILE = ..., harvest._KIBORG_TG_SESSION = ...) и подмодули
# (import harvest; harvest.X) продолжали работать. Патч переписывает module global фасада.
# НЕ switchingать подмодули на `config.X` напрямую — это сломало бы patch-target'ы.
# Обоснование значений (история решений про SOURCE_N/TELEGRAM_CHANNELS/...) — в config.py.
# `import config` + `_X = config.Y` (а НЕ `from config import ... as X`): ruff I001 при автофиксе
# схлопывал большой `from config import (...)` и удалял алиасы; assignment-строки он не трогает.
import config  # noqa: E402  # isort: skip

# DATA: cyborg/data (логи, state, статус, курсор).
DATA = config.CYBORG_DATA_DIR
# _IE_DATA: idea_engine/data (инбокс, Store). Автосбор доставляет в ИНБОКС, НЕ в копилку.
_IE_DATA = config.IDEA_ENGINE_DATA_DIR
# STATE_FILE: gate-отпечаток ленты («есть что новое?»). Mutable для test_harvest.
STATE_FILE = config.HARVEST_STATE_FILE
# STATUS_FILE: живой per-source статус для пульта.
STATUS_FILE = config.SOURCE_STATUS_FILE
# SOURCE_N: бюджет заголовков за прогон (105 // 21 канал = 5 постов с каждого).
SOURCE_N = config.SOURCE_N
# TELEGRAM_CHANNELS: 21 канал kiborg-тематика (tproger + 20 AI-каналов).
TELEGRAM_CHANNELS = config.TELEGRAM_CHANNELS
# USER_VERIFIED_SOURCES: метаданные доверия (β-метка в пульте: telegram/files проверены юзером).
USER_VERIFIED_SOURCES = config.USER_VERIFIED_SOURCES
# _DARBOT_ENV: чужой .env darbot (TG_API_ID/HASH, read-only). Mutable для test_harvest.
_DARBOT_ENV = config.DARBOT_ENV
# _KIBORG_TG_SESSION: своя копия tg-сессии (не та, что живой darbot-бот держит). Mutable для test_harvest.
_KIBORG_TG_SESSION = config.KIBORG_TG_SESSION
# Реэкспорт из подмодулей: сохраняет публичный API harvest.* для внешних потребителей
# (run.py, observe_sources.py, ВСЕ тесты). E402 — импорты после sys.path-хака выше;
# F401 — символы реэкспортируются, но в ЭТОМ модуле напрямую не используются.
from harvest_env import (  # noqa: E402,F401
    _active_sources,
    _harvest_env,
    _load_darbot_tg_creds,
    _source_env,
    wire_council,
)
from harvest_gate import (  # noqa: E402,F401
    _atomic_write,
    _last_sig,
    _persist_status,
    _save_sig,
    _should_run,
    _source_signature,
    _status_from_out,
    _titles_sig,
)
from harvest_log import _degrade_note, _log, council_note  # noqa: E402,F401
from harvest_runner import main  # noqa: E402,F401
from orchestrator import Cyborg  # noqa: E402,F401
from organs_vendored import scrub_secrets  # noqa: E402,F401
from wiring import _collect_locked, build_organs  # noqa: E402,F401  (цепочка + фетч под замком tg-сессии)

if __name__ == "__main__":
    main(sys.argv[1:])
