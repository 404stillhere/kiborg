"""ФАСАД обвязки исполняемых органов беты.

Раньше это был монолит (~465 строк): здесь жили и обёртки _run_* для органов, и сборка
реестра build_organs, и хелперы совета/вычистки/курсора. Теперь монолит разбит на 7
коротких подмодулей по одной зоне ответственности каждый:

    wiring_runtime.py  — _content_llm (выбор живой модели из env)
    wiring_collect.py  — _collect_locked, _run_collect (ГЛАЗА + замок tg-сессии)
    wiring_ideate.py   — _run_ideate (МОЗГ-генератор)
    wiring_council.py  — _IntuitionNoCap, _council_no_cap, _rank_by_council, _run_rank,
                          _run_readability (СОВЕТ на отборе + редактор читаемости)
    wiring_finish.py   — _run_finish (НОГИ: «доделай» + курсор ротации)
    wiring_scrub.py    — _liver_clean, _run_deliver, _run_finish_sink, _run_scrub
                          (ПЕЧЕНЬ + РУКА: вычистка секретов + доставка)
    wiring_builder.py  — build_organs (сборка публичного реестра из _run_*)

Этот файл (wiring.py) ОСТАЁТСЯ точкой входа для внешних потребителей (run.py, harvest.py,
panel/serve.py, все тесты): сохраняет (1) sys.path-хак для idea_engine/, (2) импорт органов
как атрибутов wiring.* (патчатся в тестах: wiring.collect_source.run, wiring.mind.deliberate,
wiring.finish_step, ...), (3) константы-мутабельные алиасы из config.py (RECON, SKIP_FOLDERS,
_TG_LOCK_TIMEOUT, _CURSOR_FILE) — патч `wiring._CURSOR_FILE = tmp` в тесте переписывает
module global, и подмодуль (wiring_finish) видит новое значение через `import wiring; wiring.X`.
И реэкспортит все публичные символы из подмодулей.

Подключены органы idea_engine (локальны, чисты, безопасны: без секретов, без записи в прод).
Реестр _shared/organs.json (89 карточек) — это каталог; сюда по одному переносятся реальные
исполняемые органы (совет: расти группами, а не подключать все 47 сразу).
"""

import os
import sys

# path-bootstrap: добавляет cyborg/ и idea_engine/ в sys.path идемпотентно.
# Вынесено в общий модуль bootstrap_paths.py, чтобы И wiring, И harvest работали АВТОНОМНО
# (раньше harvest полагался на то, что wiring добавит idea_engine/ — `import harvest` без
# `import wiring` падал на `import rejected`). Подробности — в bootstrap_paths.py.
# Сначала добавляем свой каталог (чтобы `import bootstrap_paths` резолвился), потом зовём.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootstrap_paths  # noqa: E402

bootstrap_paths.ensure_project_paths()

# Органы здесь импортируются НЕ для прямого использования в фасаде (код уехал в подмодули),
# а чтобы остаться атрибутами wiring.* — их патчат тесты (wiring.collect_source.run,
# wiring.mind.deliberate, wiring.finish_step, ...) и читают подмодули через `import wiring`.
# Потому F401 на каждом. E402 — импорты после sys.path-хака выше.
import advisors  # noqa: E402,F401  (три советника: арбитр rank_ideas + интуиция ask_llm + оркестр)
import deliver  # noqa: E402,F401  (cyborg/deliver.py — sink в инбокс idea_engine)
import finish_sink  # noqa: E402,F401  (sink: доводит nudge «доделай» до инбокса, вычистив секреты)
import mind  # noqa: E402,F401  (движок взвешенного совещания — отбор идей советом, не одним судьёй)
import seen_items  # noqa: E402,F401  (фильтр «уже видели» по ID сырых items — только для харвеста)
from core import Organ  # noqa: E402,F401
from organs import collect_source, finish_step, ideate, rank_ideas, readability_gate  # noqa: E402,F401
from organs_vendored import scrub_secrets  # noqa: E402,F401  (вендорен из реестра, чистый)
from store import state_lock  # noqa: E402,F401  (O_EXCL-замок; тот же примитив, что вокруг state.json)

# Константы — мутабельные алиасы из единого config.py (источник истины). Имена те же, что и
# раньше, чтобы тесты (wiring._CURSOR_FILE = ..., wiring._TG_LOCK_TIMEOUT = ...) и подмодули
# (import wiring; wiring.X) продолжали работать. Патч переписывает module global фасада.
# НЕ switchingать подмодули на `config.X` напрямую — это сломало бы patch-target'ы.
# `import config` + `_X = config.Y` (а НЕ `from config import ... as X`): ruff I001 при автофиксе
# схлопывал `from config import (...)` и удалял алиасы; assignment-строки он не трогает.
# `# isort: skip` на import config: иначе ruff I001 пытается слить его с блоком органов выше.
import config  # noqa: E402  # isort: skip

# RECON: backlog проектов для finish_step «доделай». Читается wiring_finish → finish_step.run.
RECON = config.RECON_FILE
# SKIP_FOLDERS: folder'ы, которые режим «доделай» не толкает (пусто = не фильтровать). Knob.
SKIP_FOLDERS = config.SKIP_FOLDERS
# Телеграм-сессия (pyrogram/SQLite) не терпит двух процессов разом ('database is locked'):
# гейт-проба, живой прогон и внешний CLI могут пересечься на одном .session. Сериализуем ДОСТУП
# O_EXCL-замком (тот же примитив, что вокруг state.json) — второй процесс ЖДЁТ освобождения, а
# не коллизится. Таймаут > фетча (телеграм-таймаут ~90с), чтобы ждущий дождался. Нет телеграма
# (нет telegram_session) → без замка, как раньше.
_TG_LOCK_TIMEOUT = config.TG_LOCK_TIMEOUT  # mutable для тестов (test_wiring ставит 0.2)
# Порог протухания lock-файла tg-сессии (СЕКУНДЫ; config хранит в минутах для читаемости).
# _collect_locked перед захватом зовёт _remove_stale_lock(sess, ...): lock старше порога
# (зависший после краша) сносится, не тратя TG_LOCK_TIMEOUT на ожидание. Mutable для тестов.
_STALE_LOCK_MAX_AGE = config.STALE_LOCK_MAX_AGE_MINUTES * 60
# Курсор ротации finish_step — куда писать/откуда читать next_cursor. Mutable для test_registry.
_CURSOR_FILE = config.CURSOR_FILE

# Реэкспорт из подмодулей: сохраняет публичный API wiring.* для внешних потребителей
# (run.py, harvest.py, panel/serve.py, ВСЕ тесты). E402 — импорты после sys.path-хака выше;
# F401 — символы реэкспортируются, но в ЭТОМ модуле напрямую не используются.
from wiring_builder import build_organs  # noqa: E402,F401
from wiring_collect import _collect_locked, _remove_stale_lock, _run_collect  # noqa: E402,F401
from wiring_council import (  # noqa: E402,F401
    _council_no_cap,
    _IntuitionNoCap,
    _rank_by_council,
    _run_rank,
    _run_readability,
)
from wiring_finish import _run_finish  # noqa: E402,F401
from wiring_ideate import _run_ideate  # noqa: E402,F401
from wiring_runtime import _content_llm  # noqa: E402,F401
from wiring_scrub import _liver_clean, _run_deliver, _run_finish_sink, _run_scrub  # noqa: E402,F401
