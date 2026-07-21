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
wiring.finish_step, ...), (3) константы (RECON, SKIP_FOLDERS, _TG_LOCK_TIMEOUT, _CURSOR_FILE),
и реэкспортит все публичные символы из подмодулей. Подмодули обращаются к органам/константам
через `import wiring; wiring.X` — так патч `wiring.X = mock` в тестах доходит до живого кода.

Подключены органы idea_engine (локальны, чисты, безопасны: без секретов, без записи в прод).
Реестр _shared/organs.json (89 карточек) — это каталог; сюда по одному переносятся реальные
исполняемые органы (совет: расти группами, а не подключать все 47 сразу).
"""

import os
import sys

# idea_engine/ — родственный пакет (органы collect_source/ideate/rank_ideas/...).
# Раньше был захардкожен абсолютным Windows-путём (M:/projects/kiborg/idea_engine) —
# ломал CI на Linux. Делаем относительным от __file__: cyborg/../idea_engine.
_IDEA = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "idea_engine"))
if _IDEA not in sys.path:
    sys.path.insert(0, _IDEA)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

RECON = "M:/projects/panelofprojects/recon.json"
SKIP_FOLDERS = []  # folder'ы, которые режим B не толкает (пусто = не фильтровать); knob finish_step


# Телеграм-сессия (pyrogram/SQLite) не терпит двух процессов разом ('database is locked'):
# гейт-проба, живой прогон и внешний CLI могут пересечься на одном .session. Сериализуем ДОСТУП
# O_EXCL-замком на файле сессии (тот же примитив, что вокруг state.json) — второй процесс ЖДЁТ
# освобождения, а не коллизится. Таймаут > фетча (телеграм-таймаут ~90с), чтобы ждущий дождался,
# а не прошёл вслепую. Замороженный collect_source НЕ трогаем — оборачиваем его ВЫЗОВ. Нет
# телеграма (нет telegram_session) → без замка, как раньше.
_TG_LOCK_TIMEOUT = 130.0

_CURSOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "finish_cursor.json")


# Реэкспорт из подмодулей: сохраняет публичный API wiring.* для внешних потребителей
# (run.py, harvest.py, panel/serve.py, ВСЕ тесты). E402 — импорты после sys.path-хака выше;
# F401 — символы реэкспортируются, но в ЭТОМ модуле напрямую не используются.
from wiring_builder import build_organs  # noqa: E402,F401
from wiring_collect import _collect_locked, _run_collect  # noqa: E402,F401
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
