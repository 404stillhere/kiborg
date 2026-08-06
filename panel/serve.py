"""Пульт киборга — локальный веб-интерфейс: что киборг умеет и что происходит внутри.

Только чтение состояния + два действия, что и так есть в CLI:
  - разобрать идею (take/later/trash) — через idea_engine/run.py status
  - запустить прогон — через cyborg/run.py "<цель>" (вывод стримится в браузеру)

Только stdlib, без venv. Слушает ТОЛЬКО loopback. Ключ LLM не читает —
проверяет лишь его наличие через ask_llm.available().

Запуск:  python panel/serve.py   →  http://{config.PANEL_HOST}:{config.PANEL_PORT}
"""

# isort: skip_file
# Этот файл намеренно нарушает isort/ruff I001: порядок импортов зависит от RUNTIME
# (wiring при импорте кладёт idea_engine/ в sys.path, поэтому rejected/organs идут ПОСЛЕ него).
# См. подробный комментарий у блока import wiring ниже. Не переупорядочивать.

import atexit
import datetime
import json
import mimetypes
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # консоль Windows бывает cp1251
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# HERE — каталог panel/ (для статических index.html/bodies.js). Локальная ответственность
# serve.py, не переносится в config (там нет panel-специфики вне PANEL_DIR).
HERE = os.path.dirname(os.path.abspath(__file__))

# Панель v2 (новый дизайн): /, /index.html, /style.css, /app.js отдаются из panel/v2/.
# Старый index.html и bodies.js продолжают отдаваться из panel/ — не ломаем v1.
STATIC_DIR = os.path.join(HERE, "v2")

# path-bootstrap: единый с wiring/harvest механизм. serve.py лежит в panel/, а не в cyborg/,
# поэтому bootstrap_paths/config напрямую не резолвятся — сначала добавляем CYBORG в sys.path
# локально (одна строка, через HERE/.. — то же значение, что config.CYBORG_DIR), потом зовём
# ensure_project_paths() (она добавит и cyborg/, и idea_engine/ идемпотентно). После этого
# `import config` работает, и мы берём оттуда остальные константы.
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "cyborg")))
import bootstrap_paths  # noqa: E402

bootstrap_paths.ensure_project_paths()
bootstrap_paths.ensure_data_dirs()  # создать data dirs на свежем клоне (serve пишет в auto.json)

# Константы из единого config.py (источник истины). CYBORG/AUTO_FILE/LAB_ROUTER — мутабельные
# алиасы: live-код serve.py читает их БЭАР-НЕЙМ (module globals), патчи в тестах
# (`serve.CYBORG = tmp`, `serve.AUTO_FILE = tmp`, `serve.LAB_ROUTER = tmp`) переписывают
# эти globals. Потому `X = config.Y` (assignment) — ruff I001 не трогает assignment-строки
# (в отличие от `from config import ... as X`, который схлопывался при автофиксе). См. config.py.
import config  # noqa: E402  # isort: skip

CYBORG = config.CYBORG_DIR  # каталог cyborg/ — subprocess cwd + чтение data-файлов (mutable)
IDEA = config.IDEA_ENGINE_DIR  # каталог idea_engine/ — subprocess cwd (status инбокса)
REGISTRY = config.ORGANS_CATALOG  # внешний каталог органов (только прод-машина, на CI нет)
LAB_ROUTER = config.LAB_ROUTER_FILE  # feature-lab статус (mutable — патчится в test_serve)
PORT = config.PANEL_PORT  # локальный HTTP на loopback
RUN_TIMEOUT = config.RUN_TIMEOUT_SEC  # watchdog на прогон (1200с = 20 мин)
AUTO_FILE = config.AUTO_JSON  # рубильник авто-режима (mutable — патчится в test_serve/test_serve_routes)

import ask_llm  # noqa: E402  (только available() — ключ не читаем и не показываем)
import council_config  # noqa: E402  (рубильники совета: rank_ideas, ask_llm, orchestra)
import direction  # noqa: E402  (руль темы: чтение/запись cyborg/data/direction.json)
import feeds  # noqa: E402  (ленты-источник: какие ленты включены, тумблеры пульта, cyborg/data/feeds.json)
import folders  # noqa: E402  (папки-источник: чтение/запись cyborg/data/folders.json)
import genparams  # noqa: E402  (параметры генерации: gen_k/rank_keep/source_n/пороги, cyborg/data/genparams.json)
import keychain  # noqa: E402  (живой состав цепочки для шапки: id'ы плеч, БЕЗ значений ключей)
import lock_monitor  # noqa: E402  (счётчик таймаутов state_lock за час — в /api/health)

# ВАЖНО: порядок критичен. wiring при импорте кладёт idea_engine/ в sys.path, поэтому
# rejected (живёт в idea_engine/) и collect_source (organs/ — тоже idea_engine/) импортируем
# ПОСЛЕ wiring. Ruff I001 сортирует по алфавиту и ломает этот порядок — поэтому noqa на каждой
# строке И isort-sorting отключена здесь вручную (нарушение умышленное, не переупорядочивать).
from wiring import build_organs  # noqa: E402  (метаданные органов; импорт чистый)
import rejected  # noqa: E402  (счётчик отклонённых для пульта; idea_engine на path через wiring)
import triage_store  # noqa: E402  (taken/later — разобранные идеи для пульта)
from organs import (  # noqa: E402  (проба папок: probe_paths — путь валиден? сколько файлов?)
    collect_source,
)

_ORGANS = build_organs()

# --- текущий прогон (один за раз) ---
RUN = {"running": False, "goal": None, "lines": [], "rc": None, "started": 0.0}
_LOCK = threading.Lock()
_PROC = {"p": None}  # текущий Popen (не сериализуем в JSON — держим отдельно от RUN)
# RUN_TIMEOUT объявлен выше в блоке констант (= config.RUN_TIMEOUT_SEC). Раньше был тут с
# комментарием-обоснованием: «режим максимум качества, совет судит 12 кандидатов (7 рецензентов
# × 12) — дольше прежних 6, поэтому потолок поднят с 600. Страховка от висяка сети, НЕ лимит на
# нормальный прогон. Дольше 20 мин — точно зависло». См. config.RUN_TIMEOUT_SEC.


def _start_proc(goal, args):
    """Запустить в CYBORG подпроцесс [python *args], стримить вывод в RUN. Один прогон за раз."""
    with _LOCK:
        if RUN["running"]:
            return False
        RUN.update(running=True, goal=goal, lines=[], rc=None, started=time.time())

    def worker():
        env = dict(os.environ, PYTHONIOENCODING=config.PYTHONIOENCODING_UTF8)
        p = None

        def _watchdog():  # прогон завис дольше RUN_TIMEOUT — убиваем, чтобы пульт не залип
            if p is not None and p.poll() is None:
                with _LOCK:
                    RUN["lines"].append(f"[пульт] прогон дольше {RUN_TIMEOUT}с — остановлен (сеть?)")
                try:
                    p.kill()
                except Exception:
                    pass

        try:
            p = subprocess.Popen(
                [sys.executable, *args],
                cwd=CYBORG,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding=config.HTTP_CHARSET_UTF8,
                errors=config.HTTP_DECODE_ERRORS_REPLACE,
                env=env,
            )
            with _LOCK:
                _PROC["p"] = p
            timer = threading.Timer(RUN_TIMEOUT, _watchdog)
            timer.daemon = True
            timer.start()
            try:
                for line in p.stdout:
                    with _LOCK:
                        RUN["lines"].append(line.rstrip("\n"))
                p.wait()
                rc = p.returncode
            finally:
                timer.cancel()
        except Exception as e:
            with _LOCK:
                RUN["lines"].append(f"[пульт] не смог запустить: {e}")
            rc = -1
        with _LOCK:
            RUN["running"] = False
            RUN["rc"] = rc
            _PROC["p"] = None

    threading.Thread(target=worker, daemon=True).start()
    return True


def _stop_run():
    """Кнопка «стоп»: убивает текущий подпроцесс (если есть). Сам worker() увидит
    p.wait() вернувшимся, допишет rc и снимет running — здесь только просим остановиться."""
    with _LOCK:
        p = _PROC["p"]
        running = RUN["running"]
    if not running or p is None or p.poll() is not None:
        return False
    try:
        p.kill()
    except Exception:
        pass
    with _LOCK:
        RUN["lines"].append("[пульт] остановлено по кнопке «стоп»")
    return True


def _start_run(goal):
    return _start_proc(goal, ["run.py", goal])


def _start_oracle(goal, project_path):
    """Запуск Oracle-режима: фиксированная цепочка scan → plan → deliver."""
    return _start_proc(
        f"oracle: {goal}",
        ["run.py", "--mode", "oracle", "--project", project_path, "--goal", goal],
    )


def _start_observe():
    """Наблюдательный обход органа-источника по кнопке — печатает от первого лица
    (зашёл в паблик → прочитал пост → подумал) в тот же живой вывод, что и прогоны.
    Read-only: зовёт орган-источник как есть, в копилку ничего не пишет."""
    return _start_proc("наблюдаю за источниками", ["observe_sources.py"])


# --- автономный режим (рубильник): фон гоняет ТОТ ЖЕ сбор по таймеру ---
# AUTO_FILE объявлен выше в блоке констант (= config.AUTO_JSON).
_AUTO = {"last": 0.0}
_AUTO_MIN = config.AUTO_INTERVAL_MIN_MINUTES
_AUTO_MAX = config.AUTO_INTERVAL_MAX_MINUTES
_AUTO_DEFAULT = config.AUTO_INTERVAL_DEFAULT_MINUTES
_AUTO_LOOP_SLEEP = config.AUTO_LOOP_SLEEP_SECONDS


def _load_auto():
    try:
        with open(AUTO_FILE, encoding=config.HTTP_CHARSET_UTF8) as f:
            d = json.load(f)
        iv = int(d.get("interval_min", _AUTO_DEFAULT))
        return {"on": bool(d.get("on")), "interval_min": max(_AUTO_MIN, min(iv, _AUTO_MAX))}
    except Exception:
        return {"on": False, "interval_min": _AUTO_DEFAULT}


def _save_auto(on, interval_min):
    iv = max(_AUTO_MIN, min(int(interval_min), _AUTO_MAX))
    tmp = AUTO_FILE + config.ATOMIC_TMP_SUFFIX
    with open(tmp, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
        json.dump({"on": bool(on), "interval_min": iv}, f, ensure_ascii=False)
    os.replace(tmp, AUTO_FILE)  # атомарно: обрыв записи не бьёт существующий флаг
    return {"on": bool(on), "interval_min": iv}


def _auto_tick():
    """Один тик авто-петли: автономность вкл + пора по интервалу + прогон не идёт → запустить
    автосбор. Возвращает True, если запустил (иначе False). Вынесено из _auto_loop ради
    тестируемости (петля = sleep + этот вызов под try/except)."""
    st = _load_auto()
    if not st["on"]:
        return False
    if time.time() - _AUTO["last"] < st["interval_min"] * config.SECONDS_PER_MINUTE:
        return False
    with _LOCK:
        busy = RUN["running"]
    if busy:
        return False
    _AUTO["last"] = time.time()
    _start_proc("автосбор идей (по расписанию)", ["harvest.py", "1"])
    return True


def _auto_loop():
    """Фон-рубильник: пока автономность включена, раз в interval_min запускает автосбор
    (harvest.py БЕЗ --force → гейт «есть что нового?» сам пропускает пустые прогоны). Один
    прогон за раз — уважает тот же RUN-замок, что и кнопки. Выключен — просто спит.
    Тик под try/except: сбой ОДНОГО тика НЕ должен завершить поток-демон (иначе автономный
    режим МОЛЧА встанет до рестарта пульта) — логируем и продолжаем со следующего тика."""
    _AUTO["last"] = time.time()  # не палить прогон в первую же минуту после старта пульта
    while True:
        time.sleep(_AUTO_LOOP_SLEEP)
        try:
            _auto_tick()
        except Exception as e:
            print(f"[auto_loop] сбой тика (продолжаю): {type(e).__name__}: {e}", flush=True)


def _set_idea(idea_id, status):
    """Разбор идеи — через канонический CLI idea_engine (он же перерисует inbox.md)."""
    if status not in config.STORE_CLEARED_STATUSES:
        return {"ok": False, "msg": "статус должен быть take|later|trash"}
    # НЕ мутируем state.json, пока идёт прогон: deliver в подпроцессе пишет ТОТ ЖЕ файл, а триаж
    # делает свой read-modify-write — с одного снимка = lost-update (порчу-JSON снял atomic-write
    # в store.py, осталась перезапись). Пульт знает про свой прогон по RUN["running"] — на нём и
    # сериализуем (закрывает частый случай пульт-триаж || пульт-прогон; внешний CLI-harvest — вне
    # видимости пульта, для него нужен OS-замок в idea_engine, см. loose-ends).
    with _LOCK:
        if RUN["running"]:
            return {
                "ok": False,
                "busy": True,
                "msg": "идёт прогон — разбор отложен на секунду, повтори когда закончится",
            }
    env = dict(os.environ, PYTHONIOENCODING=config.PYTHONIOENCODING_UTF8)
    p = subprocess.run(
        [sys.executable, "run.py", "status", str(int(idea_id)), status],
        cwd=IDEA,
        capture_output=True,
        encoding=config.HTTP_CHARSET_UTF8,
        errors=config.HTTP_DECODE_ERRORS_REPLACE,
        env=env,
    )
    out = (p.stdout or "").strip() + (p.stderr or "").strip()
    return {"ok": p.returncode == 0 and "NOT_FOUND" not in out, "msg": out[: config.PANEL_MSG_MAX_CHARS]}


def _purge_low_score(max_score):
    """Массовый триаж: все открытые идеи с score < max_score → мусор.

    Один вызов = один read state.json + N вызовов канонического _set_idea(id, "trash")
    (по одному subprocess на идею — как кнопки в UI, только пакетом). Каждый _set_idea
    сам проверяет RUN["running"], поэтому если стартанул прогон во время очистки —
    оставшиеся идеи будут отбиты busy (частичная очистка безопасна: state.json под state_lock).
    Возвращает статистику: сколько зачищено, сколько ошиблось, порог.
    """
    if not (config.SCORE_MIN <= max_score <= config.SCORE_MAX):
        return {"ok": False, "msg": "max_score должен быть в диапазоне 0..10"}
    with _LOCK:
        if RUN["running"]:
            return {
                "ok": False,
                "busy": True,
                "msg": "идёт прогон — дождись окончания, потом очищай",
            }
    # читаем снимок state.json, чтобы найти кандидатов (сам триаж идёт через _set_idea)
    try:
        with open(config.IE_STATE_JSON, encoding=config.HTTP_CHARSET_UTF8) as f:
            state = json.load(f)
    except Exception as e:
        return {"ok": False, "msg": f"state.json не читается: {str(e)[: config.PANEL_ERROR_MAX_CHARS]}"}

    candidates = []
    for idea in state.get("ideas", []):
        if idea.get("status") != config.STORE_STATUS_OPEN:
            continue
        score = idea.get("score")
        if score is None:
            continue  # без оценки — не трогаем, оставляем на ручной разбор
        try:
            sc = float(score)
        except (TypeError, ValueError):
            continue
        if sc < max_score:
            candidates.append(idea["id"])

    if not candidates:
        return {
            "ok": True,
            "purged": 0,
            "failed": 0,
            "threshold": max_score,
            "msg": "нет открытых идей с оценкой ниже порога",
        }

    purged, failed = 0, []
    for idea_id in candidates:
        r = _set_idea(idea_id, config.STORE_STATUS_TRASH)
        if r.get("ok"):
            purged += 1
        else:
            # busy посреди цикла (пошёл прогон) — останавливаемся, оставшиеся ждут
            if r.get("busy"):
                failed.append({"id": idea_id, "msg": "прогон стартовал — пропущено"})
                break
            failed.append({"id": idea_id, "msg": (r.get("msg") or "")[: config.PANEL_PURGE_MSG_MAX_CHARS]})
    return {
        "ok": True,
        "purged": purged,
        "failed": len(failed),
        "failed_details": failed[: config.PANEL_PURGE_FAILED_DETAILS_MAX],
        "threshold": max_score,
        "candidates": len(candidates),
    }


_RUN_LINE = re.compile(r"^- \[(?P<ts>[^\]]+)\] «(?P<goal>.*?)» → (?P<chain>.*?) \| (?P<res>.*)$")


def _read_runs():
    runs = []
    try:
        with open(os.path.join(CYBORG, "data", config.RUNS_MD_FILE), encoding=config.HTTP_CHARSET_UTF8) as f:
            for line in f:
                m = _RUN_LINE.match(line.strip())
                if not m:
                    continue
                res = m.group("res")
                degraded = None
                if " | ⚠ " in res:
                    res, degraded = res.split(" | ⚠ ", 1)
                council = None
                if " | совет: " in res:  # опциональный хвост вердикта от арбитра
                    res, council = res.split(" | совет: ", 1)
                key, _, val = res.partition("=")
                runs.append(
                    {
                        "ts": m.group("ts"),
                        "goal": m.group("goal"),
                        "chain": [s.strip() for s in m.group("chain").split("->")],
                        "deliverable": key,
                        "value": val,
                        "council": council.strip() if council else None,
                        "degraded": degraded.strip() if degraded else None,
                    }
                )
    except Exception:
        pass
    return runs


def _read_source_status():
    """Живой per-source статус (cyborg/data/source_status.json) — пишется harvest'ом на
    каждом авто-прогоне (не-force). Нет файла (ещё не гоняли) -> None, пульт не показывает."""
    path = os.path.join(CYBORG, "data", config.SOURCE_STATUS_FILE_NAME)
    try:
        with open(path, encoding=config.HTTP_CHARSET_UTF8) as f:
            return json.load(f)
    except Exception:
        return None


def _active_source_names():
    """Текущий набор источников, включённых пользователем.

    source_status — это снимок прошлого фонового обхода. Тумблер ленты меняется
    мгновенно, сам файл статуса — только на следующем обходе, поэтому health обязан
    смотреть на НЫНЕШНИЙ набор, а не ругать выключенный вчера Reddit/Telegram.
    """
    active = list(feeds.enabled())
    if folders.current():
        active.append("files")
    return active


def _health():
    """Healthcheck для мониторинга/алертинга: статус ключевых компонентов в одном JSON.

    ok=True когда ВСЁ здорово: LLM-цепочка жива (есть ключи), state.json парсится (не повреждён),
    и НИ ОДИН активный источник не упал (нет error в source_status.json). Что-то одно отвалилось
    — ok=False, в соответствующем поле подробность. last_run — код возврата последнего прогона
    (rc≠0 = падение подпроцесса, None = ещё не гоняли)."""
    # LLM: ask_llm.available() = есть ли цепочка ключей (muse→deepseek→nemotron через closerouter).
    llm_ok = ask_llm.available()
    # state.json: пытаемся json.load. Повреждён/нет файла — ok=False + error.
    state_err = None
    try:
        with open(config.IE_STATE_JSON, encoding=config.HTTP_CHARSET_UTF8) as f:
            json.load(f)
    except Exception as e:
        state_err = str(e)[: config.PANEL_ERROR_MAX_CHARS]
    # Источники: per-source статус из source_status.json (если есть). Упавший = есть error,
    # но только для ленты, включённой ПРЯМО СЕЙЧАС: файл статуса может помнить старый обход.
    sources = _read_source_status()
    src_down = []
    active_sources = set(_active_source_names())
    if isinstance(sources, dict):
        for name, st in (sources.get("sources") or {}).items():
            if name in active_sources and isinstance(st, dict) and st.get("error"):
                src_down.append(name)
    ok = bool(llm_ok and state_err is None and not src_down)
    # Таймауты state_lock за последний час (после stale-lock-cleanup это РЕДКОСТЬ —
    # значит живой конкурент реально держал лок >130с). Счётчик per-process in-memory.
    recent = lock_monitor.recent_timeouts(config.PANEL_HEALTH_LOCK_WINDOW_MINUTES)
    # --- setup status (usability) ---
    keys_configured = keychain.available()
    active = list(_active_source_names())
    setup_warnings = []
    if not keys_configured:
        setup_warnings.append("API-ключи не настроены — впишите в cyborg/llm_keys.env")
    if not active:
        setup_warnings.append("Нет включённых источников — откройте настройки пульта")
    setup_status = "critical" if not keys_configured else ("warning" if not active else "ok")
    return {
        "ok": ok,
        "llm": {"available": llm_ok},
        "state_json": {"ok": state_err is None, "error": state_err},
        "sources": {"down": src_down, "status": sources},
        "last_run": {"rc": RUN.get("rc"), "running": RUN.get("running")},
        "locks": {"recent_timeouts": recent, "window_minutes": config.PANEL_HEALTH_LOCK_WINDOW_MINUTES},
        "setup": {
            "status": setup_status,
            "keys_configured": keys_configured,
            "active_sources": active,
            "warnings": setup_warnings,
        },
    }


def _read_inbox():
    try:
        with open(config.IE_STATE_JSON, encoding=config.HTTP_CHARSET_UTF8) as f:
            s = json.load(f)
        # state.json хранит только open (мастер-разделение 2026-07-22): take/later/trash физически
        # перенесены в taken.json / later.json / rejected.json при триаже. Отдаём все три списка
        # отдельными полями — UI собирает «Разобранные» из этих источников (раньше фильтровал
        # ideas[] по status). rejected раньше уходил только числом, поэтому его нельзя было
        # раскрыть в пульте.
        return {
            "cap": s.get("cap", 0),
            "tick": s.get("tick", 0),
            "ideas": s.get("ideas", []),  # только open (остальные вырезаны при триаже)
            "taken": triage_store.load(triage_store.TAKEN_PATH).get(config.TRIAGE_STORE_TAKEN_KEY, []),
            "later": triage_store.load(triage_store.LATER_PATH).get(config.TRIAGE_STORE_LATER_KEY, []),
            "rejected": rejected.load().get(config.REJECTED_KEY, []),
            "finish": s.get("finish"),
            "seen_count": len(s.get("seen", [])),
        }
    except Exception as e:
        return {
            "error": str(e)[: config.PANEL_ERROR_MAX_CHARS],
            "cap": 0,
            "tick": 0,
            "ideas": [],
            "taken": [],
            "later": [],
            "rejected": [],
            "finish": None,
            "seen_count": 0,
        }


def _read_registry():
    try:
        with open(REGISTRY, encoding=config.HTTP_CHARSET_UTF8) as f:
            cards = json.load(f).get("organs", [])
        by_status, by_project = {}, {}
        slim = []
        for c in cards:
            st = c.get("status", "?")
            pr = c.get("project", "?")
            by_status[st] = by_status.get(st, 0) + 1
            by_project[pr] = by_project.get(pr, 0) + 1
            slim.append(
                {
                    "name": c.get("name"),
                    "project": pr,
                    "status": st,
                    "purpose": (c.get("purpose") or "")[: config.PANEL_CARD_PURPOSE_MAX_CHARS],
                    "needs_keys": c.get("needs_keys") or [],
                    "language": c.get("language", ""),
                }
            )
        return {"total": len(cards), "by_status": by_status, "by_project": by_project, "cards": slim}
    except Exception as e:
        return {
            "error": str(e)[: config.PANEL_ERROR_MAX_CHARS],
            "total": 0,
            "by_status": {},
            "by_project": {},
            "cards": [],
        }


def _read_lab():
    try:
        with open(LAB_ROUTER, encoding=config.HTTP_CHARSET_UTF8) as f:
            r = json.load(f)
        feats = [
            {
                "slug": x.get("slug"),
                "title": x.get("title"),
                "status": x.get("status"),
                "reviewed": bool(x.get("reviewed")),
                "enabled": bool(x.get("enabled")),
                "why": (x.get("why") or "")[: config.PANEL_CARD_WHY_MAX_CHARS],
            }
            for x in r.get("features", [])
        ]
        locked = any(f["status"] == "ready" and not f["reviewed"] for f in feats)
        return {"exists": True, "locked": locked, "features": feats, "needs_manual": len(r.get("needs_manual", []))}
    except Exception:
        return {"exists": False, "locked": False, "features": [], "needs_manual": 0}


def _key_state():
    """Живой статус ключа для шапки: РЕАЛЬНО сконфигуренные плечи цепочки, а не статичный ярлык.
    present — есть ли хоть одно плечо; model — «muse-spark→deepseek→nemotron» по ФАКТУ заданного
    ключа (keychain.chain_ids даёт только id плеч, не трогая apiKey/baseUrl).
    Раньше отдавали статичный ask_llm._MODEL — при отсутствии ключа бейдж врал про плечи,
    которых нет (аудит 2026-07-17)."""
    ids = keychain.chain_ids()
    return {"present": bool(ids), "model": "→".join(ids) or ask_llm._MODEL}


def _last_provider():
    """Последний использованный LLM-провайдер (из cyborg/data/last_provider.json).

    Пустая строка, если ещё не было вызовов или все провайдеры молчали.
    Файл нужен вместо module-global ask_llm.last_provider, потому что serve.py
    работает в отдельном Python-процессе и не видит память клиента.
    """
    try:
        with open(config.LAST_PROVIDER_FILE, encoding=config.HTTP_CHARSET_UTF8) as f:
            return str(json.load(f).get("provider", "") or "")
    except Exception:
        return ""


def _read_oracles():
    """Список сохранённых Oracle-планов из idea_engine/data/oracles/*/YYYY-MM-DD_*.md.

    Возвращает список [{slug, goal, path, ts, steps}], отсортированный по убыванию даты.
    Без индекса — читает файлы напрямую. Быстро: планов немного.
    """
    oracles_dir = config.ORACLES_DIR
    out = []
    try:
        for slug in os.listdir(oracles_dir):
            plan_dir = os.path.join(oracles_dir, slug)
            if not os.path.isdir(plan_dir) or slug == ".":
                continue
            for fname in os.listdir(plan_dir):
                if not fname.endswith(config.ORACLE_PLAN_EXT) or fname == config.ORACLE_INDEX_FILE:
                    continue
                path = os.path.join(plan_dir, fname)
                try:
                    st = os.path.getmtime(path)
                    ts = datetime.datetime.fromtimestamp(st).strftime(config.ORACLE_PLAN_INDEX_FMT)
                except Exception:
                    ts = fname[: config.PANEL_ORACLE_FALLBACK_TS_LEN] + " 00:00"
                out.append({"slug": slug, "path": path, "ts": ts})
    except Exception:
        return []
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out[: config.PANEL_ORACLE_LIST_MAX_ITEMS]


def _api_state():
    wired = [
        {
            "name": o.name,
            "purpose": o.purpose,
            "role": o.role,
            "produces": o.produces,
            "consumes": o.consumes,
            "tags": o.tags,
            "needs": o.needs,
        }
        for o in _ORGANS
    ]
    # running/goal текущего прогона в общем state — чтобы 5-сек refresh пульта видел и ФОНОВЫЙ
    # (cron/авто) прогон, а не только ручной через pollRun (раньше /api/state его не нёс → пульт
    # показывал «отдыхает», пока киборг сам собирал по расписанию; аудит honesty 2026-07-18).
    with _LOCK:
        running, run_goal = RUN["running"], RUN["goal"]
    return {
        "now": time.strftime(config.PANEL_CLOCK_FMT),
        "running": running,
        "run_goal": run_goal,
        "key": _key_state(),
        "organs": wired,
        "inbox": _read_inbox(),
        "sources": _read_source_status(),
        "auto": _load_auto(),
        "runs": _read_runs(),
        "registry": _read_registry(),
        "lab": _read_lab(),
        "direction": direction.load(),
        "folders": folders.load(),
        "feeds": feeds.load(),
        "council": council_config.load(),
        # Параметры генерации (gen_k/rank_keep/source_n/пороги) — meta() отдаёт min/max/default/
        # is_float/value для каждого. UI строит range-инпуты по этим данным. Без файла — дефолты.
        "genparams": genparams.meta()["params"],
        "rejected": rejected.count(),  # сколько идей отклонено «мусором» (учат генератор/судью)
        "oracles": _read_oracles(),
        "last_provider": _last_provider(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # тихий сервер
        pass

    def _send(self, code, body, ctype=config.HTTP_MEDIA_TYPE_JSON_UTF8):
        raw = body if isinstance(body, bytes) else body.encode(config.HTTP_CHARSET_UTF8)
        self.send_response(code)
        self.send_header(config.HTTP_HEADER_CONTENT_TYPE, ctype)
        self.send_header(config.HTTP_HEADER_CONTENT_LENGTH, str(len(raw)))
        self.send_header(config.HTTP_HEADER_CACHE_CONTROL, config.HTTP_CACHE_CONTROL_NO_STORE)
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def serve_static(self, filename):
        """Отдать файл из STATIC_DIR (panel/v2/). Content-Type через mimetypes.guess_type.
        Кеширование:
          - HTML (index.html) НЕ кешируем (Cache-Control: no-store) — иначе браузер
            не увидит обновлений верстки после правок (баг 2026-07-22: пользователь
            открывал старый v1 из кеша, потому что ранее на / стоял max-age=3600).
          - CSS/JS кешируем на час — они версионируются через содержимое, а index.html
            ссылается на них по статичному URL; при обновлении serve.py новый index.html
            (без кеша) снова тянет их свежие версии.
        Файл отсутствует → 404 текстом (как api-роуты, без трейсбека)."""
        path = os.path.join(STATIC_DIR, filename)
        if not os.path.isfile(path):
            self._send(404, f"нет файла: {filename}", config.HTTP_MEDIA_TYPE_TEXT_PLAIN_UTF8)
            return
        ctype, _ = mimetypes.guess_type(path)
        ctype = ctype or config.HTTP_MEDIA_TYPE_OCTET_STREAM
        # На этапе активной разработки v2 отдаём всё без кеша: иначе у пользователя
        # залипает старая версия после правок и он видит «декоративные» кнопки,
        # потому что app.js не обновился (баг 2026-07-22).
        cache = config.HTTP_CACHE_CONTROL_NO_STORE
        try:
            with open(path, "rb") as f:
                raw = f.read()
            self.send_response(200)
            self.send_header(config.HTTP_HEADER_CONTENT_TYPE, f"{ctype}; charset={config.HTTP_CHARSET_UTF8}")
            self.send_header(config.HTTP_HEADER_CONTENT_LENGTH, str(len(raw)))
            self.send_header(config.HTTP_HEADER_CACHE_CONTROL, cache)
            self.end_headers()
            self.wfile.write(raw)
        except Exception as e:
            self._send(500, f"не читается: {e}", config.HTTP_MEDIA_TYPE_TEXT_PLAIN_UTF8)

    def do_GET(self):
        # ── Статика v2 (новый пульт): /, /index.html, /style.css, /app.js ──
        # Старая отдача index.html из HERE перекрыта этим блоком (v2 — приоритет),
        # но старый код ниже оставлен — не ломаем v1, просто он теперь недостижим
        # для '/' и '/index.html'. bodies.js отдаётся как и раньше из HERE.
        if self.path in config.PANEL_V2_STATIC_ROUTES:
            fname = config.PANEL_V2_INDEX_FILE if self.path in ("/", "/index.html") else self.path.lstrip("/")
            self.serve_static(fname)
            return
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, config.PANEL_V1_INDEX_FILE), encoding=config.HTTP_CHARSET_UTF8) as f:
                    self._send(200, f.read(), config.HTTP_MEDIA_TYPE_TEXT_HTML_UTF8)
            except Exception as e:
                self._send(500, f"index.html не читается: {e}", config.HTTP_MEDIA_TYPE_TEXT_PLAIN_UTF8)
        elif self.path == "/bodies.js":
            try:
                with open(os.path.join(HERE, config.PANEL_V1_BODIES_FILE), encoding=config.HTTP_CHARSET_UTF8) as f:
                    self._send(200, f.read(), config.HTTP_MEDIA_TYPE_TEXT_JAVASCRIPT_UTF8)
            except Exception as e:
                self._send(500, f"// bodies.js: {e}", config.HTTP_MEDIA_TYPE_TEXT_JAVASCRIPT_UTF8)
        elif self.path == "/api/state":
            try:
                self._json(_api_state())
            except Exception as e:
                self._json({"error": str(e)[: config.PANEL_ERROR_MAX_CHARS]}, 500)
        elif self.path == "/api/run":
            try:
                with _LOCK:
                    self._json({k: RUN[k] for k in ("running", "goal", "lines", "rc")})
            except Exception as e:
                self._json({"error": str(e)[: config.PANEL_ERROR_MAX_CHARS]}, 500)
        elif self.path == "/api/folders/probe":
            # проба текущих папок при загрузке пульта (счётчики не на каждом poll /api/state —
            # обход дорог; отдельный редкий вызов). Валиден ли путь + сколько в нём файлов.
            try:  # проба ВСЕХ папок (вкл+выкл) — счётчик файлов виден
                self._json({"probe": collect_source.probe_paths(folders.all_paths())})
            except Exception as e:
                self._json({"error": str(e)[: config.PANEL_ERROR_MAX_CHARS]}, 500)
        elif self.path == "/api/genparams":
            # метаданные параметров генерации (min/max/default/value для каждого) — UI строит
            # range-инпуты в drawer «Настройки». GET — чтение, POST — save/reset.
            try:
                self._json(genparams.meta())
            except Exception as e:
                self._json({"error": str(e)[: config.PANEL_ERROR_MAX_CHARS]}, 500)
        elif self.path == "/api/health":
            # healthcheck: статус ключевых компонентов для мониторинга/алертинга.
            # ok=True когда LLM-цепочка жива, state.json парсится, и НИ ОДИН источник не упал
            # (источник с error в source_status.json = явный сбой сети/кред — виден в /health).
            try:
                self._json(_health())
            except Exception as e:
                self._json({"ok": False, "error": str(e)[: config.PANEL_ERROR_MAX_CHARS]}, 500)
        else:
            self._json({"error": "нет такого пути"}, 404)

    def do_POST(self):
        # анти-CSRF: чужой сайт в браузере юзера не должен дёргать наши действия.
        # Браузер на POST шлёт Origin — принимаем только свой (или его отсутствие: curl/скрипты).
        origin = self.headers.get(config.HTTP_HEADER_ORIGIN, "")
        _allowed_origins = {f"http://{config.PANEL_HOST}:{PORT}", f"http://{config.PANEL_LOCALHOST_ALIAS}:{PORT}"}
        if origin and origin not in _allowed_origins:
            self._json({"ok": False, "msg": "чужой источник — отказано"}, 403)
            return
        ctype = (self.headers.get(config.HTTP_HEADER_CONTENT_TYPE) or "").lower()
        if config.HTTP_MEDIA_TYPE_JSON not in ctype:  # form-POST с text/plain сюда не пройдёт
            self._json(
                {"ok": False, "msg": f"нужен {config.HTTP_HEADER_CONTENT_TYPE}: {config.HTTP_MEDIA_TYPE_JSON}"}, 415
            )
            return
        try:
            n = int(self.headers.get(config.HTTP_HEADER_CONTENT_LENGTH) or 0)
            body = json.loads(self.rfile.read(n).decode(config.HTTP_CHARSET_UTF8)) if n else {}
        except Exception:
            self._json({"ok": False, "msg": "плохой JSON"}, 400)
            return
        if not isinstance(body, dict):
            self._json({"ok": False, "msg": "тело должно быть JSON-объектом"}, 400)
            return
        if self.path == "/api/run":
            goal = str(body.get("goal") or "").replace("\n", " ").strip()[: config.PANEL_GOAL_MAX_CHARS]
            if not goal:
                self._json({"ok": False, "msg": "пустая цель"}, 400)
                return
            ok = _start_run(goal)
            self._json({"ok": ok, "msg": "" if ok else "прогон уже идёт"})
        elif self.path == "/api/oracle":
            goal = str(body.get("goal") or "").replace("\n", " ").strip()[: config.PANEL_GOAL_MAX_CHARS]
            project = str(body.get("project") or "").strip()[: config.PANEL_ORACLE_PROJECT_MAX_CHARS]
            if not goal:
                self._json({"ok": False, "msg": "пустая цель"}, 400)
                return
            if not project:
                self._json({"ok": False, "msg": "не указан путь к проекту"}, 400)
                return
            ok = _start_oracle(goal, project)
            self._json({"ok": ok, "msg": "" if ok else "прогон уже идёт"})
        elif self.path == "/api/observe":
            ok = _start_observe()
            self._json({"ok": ok, "msg": "" if ok else "прогон уже идёт"})
        elif self.path == "/api/auto":
            try:
                iv = int(body.get("interval_min", _AUTO_DEFAULT))
            except (TypeError, ValueError):
                # как /api/idea: кривой тип → 400, а не ValueError из _save_auto (int()) вне try →
                # обрыв запроса/трейсбек. Был единственный POST-роут без валидации входа (асимметрия).
                self._json({"ok": False, "msg": "interval_min должен быть числом"}, 400)
                return
            res = _save_auto(bool(body.get("on")), iv)
            if res["on"]:
                _AUTO["last"] = 0.0  # включили — дать сработать на ближайшем тике, не ждать интервал
            self._json({"ok": True, **res})
        elif self.path == "/api/direction":
            # руль темы: current (str, "" = снять) и/или presets (list). Чистку/потолки делает direction.save.
            cur = body.get("current")
            presets = body.get("presets")
            if cur is not None and not isinstance(cur, str):
                self._json({"ok": False, "msg": "current должен быть строкой"}, 400)
                return
            if presets is not None and not isinstance(presets, list):
                self._json({"ok": False, "msg": "presets должен быть списком"}, 400)
                return
            saved = direction.save(current=cur, presets=presets)
            self._json({"ok": True, **saved})
        elif self.path == "/api/folders":
            # папки-источник: folders (list of str или {path,on}) — у каждой свой тумблер вкл/выкл.
            # Старый фронт слал "paths" (плоский список) — принимаем и его. Чистку/дедуп/нормализацию/
            # потолки делает folders.save.
            items = body.get("folders")
            if items is None:
                items = body.get("paths")  # обратная совместимость
            if not isinstance(items, list):
                self._json({"ok": False, "msg": "folders должен быть списком"}, 400)
                return
            saved = folders.save(items)
            try:  # проба ВСЕХ сохранённых папок (валиден? сколько файлов?) —
                probe = collect_source.probe_paths([f["path"] for f in saved.get("folders", [])])
            except Exception:
                probe = {}  # проба — удобство, её сбой не валит сохранение
            self._json({"ok": True, **saved, "probe": probe})
        elif self.path == "/api/feeds":
            # ленты-источник: enabled (list of str) — какие ленты включены. Чистку/дедуп/
            # только-известные/канон-порядок делает feeds.save.
            en = body.get("enabled")
            if not isinstance(en, list):
                self._json({"ok": False, "msg": "enabled должен быть списком"}, 400)
                return
            saved = feeds.save(en)
            self._json({"ok": True, **saved})
        elif self.path == "/api/council":
            # рубильники совета: enabled (list of str) — какие советники включены
            en = body.get("enabled")
            if not isinstance(en, list):
                self._json({"ok": False, "msg": "enabled должен быть списком"}, 400)
                return
            saved = council_config.save(en)
            self._json({"ok": True, **saved})
        elif self.path == "/api/genparams":
            # параметры генерации: либо reset (сброс к дефолтам), либо частичное обновление.
            # Любой посторонний ключ игнорируется (forward-compat). Clamp по диапазонам — в genparams.
            if body.get("reset"):
                genparams.reset()  # перезаписать файл дефолтами, потом отдать meta (с новыми value)
                self._json({"ok": True, "params": genparams.meta()["params"]})
                return
            genparams.save(body)
            self._json({"ok": True, "params": genparams.meta()["params"]})
        elif self.path == "/api/stop":
            ok = _stop_run()
            self._json({"ok": ok, "msg": "" if ok else "нечего останавливать"})
        elif self.path == "/api/idea":
            try:
                idea_id = int(body.get("id"))
            except (TypeError, ValueError):
                self._json({"ok": False, "msg": "нужен числовой id идеи"}, 400)
                return
            try:
                res = _set_idea(idea_id, str(body.get("status")))
            except Exception as e:
                res = {"ok": False, "msg": ("не вышло: " + str(e))[: config.PANEL_MSG_MAX_CHARS]}
            self._json(res)
        elif self.path == "/api/ideas/purge":
            # массовый триаж: все открытые идеи с score < threshold → мусор.
            # threshold по умолчанию 8.0 (зелёный круг = хорошая идея, ниже = на разбор).
            try:
                threshold = float(body.get("threshold", config.DEFAULT_READ_MIN_SCORE))
            except (TypeError, ValueError):
                self._json({"ok": False, "msg": "threshold должен быть числом"}, 400)
                return
            try:
                res = _purge_low_score(threshold)
            except Exception as e:
                res = {"ok": False, "msg": ("не вышло: " + str(e))[: config.PANEL_MSG_MAX_CHARS]}
            self._json(res)
        else:
            self._json({"error": "нет такого пути"}, 404)


def _check_port_available(host: str, port: int) -> bool:
    """Проверяет, свободен ли порт."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def main():
    if not _check_port_available(config.PANEL_HOST, PORT):
        print(
            f"⚠ Порт {PORT} уже занят — возможно, пульт уже запущен.\n"
            f"   Закройте другой экземпляр или измените порт в config.py.",
            file=sys.stderr,
        )
        sys.exit(1)

    srv = ThreadingHTTPServer((config.PANEL_HOST, PORT), Handler)
    threading.Thread(target=_auto_loop, daemon=True).start()  # фон-рубильник (по умолчанию выключен)
    print(f"Пульт киборга: http://{config.PANEL_HOST}:{PORT}  (Ctrl+C — стоп)")

    # graceful shutdown: по SIGTERM/SIGINT — остановить run-процесс и сервер
    def _shutdown(signum, frame):
        print(f"[panel] получен signal {signum} — корректная остановка")
        _stop_run()
        srv.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    atexit.register(_stop_run)  # страховка на случай неожиданного выхода

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass  # уже обработано в _shutdown
    finally:
        _stop_run()


if __name__ == "__main__":
    main()
