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
import keychain  # noqa: E402,F401  (ключи -> совет на отборе; впаивается wire_council для ОБЕИХ кнопок)
import rejected  # noqa: E402,F401  (отклонённые: env["rejected"] — генератор/судья не приносят похожее; idea_engine на path через wiring)
import seen_items  # noqa: E402,F401
from orchestrator import Cyborg  # noqa: E402,F401
from organs_vendored import scrub_secrets  # noqa: E402,F401
from wiring import _collect_locked, build_organs  # noqa: E402,F401  (цепочка + фетч под замком tg-сессии)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# автосбор доставляет в ИНБОКС idea_engine (через deliver), НЕ в копилку — отчёт строим по инбоксу
_IE_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "idea_engine", "data")
STATE_FILE = os.path.join(DATA, "harvest_state.json")
STATUS_FILE = os.path.join(DATA, "source_status.json")  # живой per-source статус для пульта

# Сколько заголовков тянуть за прогон СУММАРНО (бюджет делится между источниками в
# collect_source). Дефолт органа collect_source — 8; [D] беру 30: КОРЕНЬ «копилка застряла
# на 6» — узкий источник (топ-8 HN меняется раз в часы → те же идеи → дедуп режет). Шире
# слой = разнообразнее сырьё для ideate + гейт видит churn глубже (меньше холостых
# пропусков). Это КОНФИГ (мой файл), а не правка ядра: collect_source читает n/sources из
# env по дизайну. Тюнить — здесь.
# Поднят 30→105 (режим «максимум качества», деньги/время не важны): 105 // 21 канал = 5 свежих
# постов с каждого → ~105 заголовков-семян вместо 21. Больше и разнообразнее сырья для ideate.
SOURCE_N = 105

# Источники, что мержим за один прогон. Product Hunt отложен — нужен токен (гейт юзера).
# "telegram" — КЛЮЧЕВОЙ источник: читает каналы через личный ТГ-аккаунт (орган collect_tg_news,
# вендорен из darbot). Креды/сессия резолвятся в _harvest_env ниже — без них telegram сам себя
# выключает (ValueError "no channels", errors, не крашит прогон).
#
# Какие ленты ВКЛЮЧЕНЫ — теперь решает юзер тумблерами в пульте (cyborg/feeds.py, data/feeds.json),
# а не константа тут (2026-07-14). Дефолт (нет файла) = feeds.DEFAULT_FEEDS = ["telegram"] — то же
# поведение, что было захардкожено. Историю урезания охвата 5→1 (2026-07-13) заменил живой тумблер.

# Папки-источник (2026-07-14): киборг читает текстовые файлы из заданных папок как ещё одно
# СЫРЬЁ и смотрит на них НЕЙТРАЛЬНО — как на чужой проект со стороны (не «свой код», без «чини
# себя»: так идеи честнее). Список папок живёт в data/folders.json и правится В ПУЛЬТЕ мышкой
# (см. cyborg/folders.py) — пусто = источник «files» выключен. Секреты (*.env/*.session/ключи)
# и мусор (.git/venv/node_modules/__pycache__) орган пропускает сам (collect_source._files).


# Каналы под тематику kiborg (тех/AI/pet-проекты) — НЕ список darbot (тот про новости/политику/
# экономику, другая тема). @tproger — мой стартовый кандидат, подтверждён живым смоуком 2026-07-12.
# 21 канал: @tproger (стартовый, подтверждён живым смоуком) + 20 из папки юзера
# (t.me/addlist/gUpAozY8_SI0ZTVi, тема "AI 🤖"), разрешена read-only (chatlists.CheckChatlistInvite
# — НЕ подписка, только просмотр состава) 2026-07-12, все настоящие, список подтверждён живым 2026-07-13.
# История охвата: 2026-07-12 урезан до 1 канала для наглядного наблюдения органа источников →
# 2026-07-13 второй → 2026-07-13 ВОЗВРАЩЁН полный охват (20 AI + tproger) по просьбе юзера.
# Список длиннее бюджета n — _telegram() берёт случайную выборку каждый прогон (ротация по времени).
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

# Какие источники ЛИЧНО проверены юзером (не «бета»). Пока — только telegram: каналы юзер
# сам курировал из своей папки, @tproger подтвердил живым смоуком. Остальные 4 (hn/reddit/
# lobsters/gh_trending) подключены, крутятся, но юзером персонально НЕ провалидированы —
# пульт метит их «β» (бета). Это метаданные доверия, не живой статус: живой 🟢/🔴 считает
# _status_from_out по фактическому улову, а beta — статичный признак отсюда. Расширять по мере
# того, как юзер подтверждает источник вручную.
USER_VERIFIED_SOURCES = {"telegram", "files"}  # files — свои папки юзера, не «бета»

_DARBOT_ENV = "M:/projects/darbot/.env"
_KIBORG_TG_SESSION = os.path.join(DATA, "kiborg_tg.session")


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

if __name__ == "__main__":
    main(sys.argv[1:])
