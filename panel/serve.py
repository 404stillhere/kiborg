"""Пульт киборга — локальный веб-интерфейс: что киборг умеет и что происходит внутри.

Только чтение состояния + два действия, что и так есть в CLI:
  - разобрать идею (take/later/trash) — через idea_engine/run.py status
  - запустить прогон — через cyborg/run.py "<цель>" (вывод стримится в браузеру)

Только stdlib, без venv. Слушает ТОЛЬКО 127.0.0.1. Ключ LLM не читает —
проверяет лишь его наличие через ask_llm.available().

Запуск:  python M:/projects/kiborg/panel/serve.py   →  http://127.0.0.1:8737
"""

# isort: skip_file
# Этот файл намеренно нарушает isort/ruff I001: порядок импортов зависит от RUNTIME
# (wiring при импорте кладёт idea_engine/ в sys.path, поэтому rejected/organs идут ПОСЛЕ него).
# См. подробный комментарий у блока import wiring ниже. Не переупорядочивать.

import json
import os
import re
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
PORT = config.PANEL_PORT  # 8737, локальный HTTP на 127.0.0.1
RUN_TIMEOUT = config.RUN_TIMEOUT_SEC  # watchdog на прогон (1200с = 20 мин)
AUTO_FILE = config.AUTO_JSON  # рубильник авто-режима (mutable — патчится в test_serve/test_serve_routes)

import ask_llm  # noqa: E402  (только available() — ключ не читаем и не показываем)
import council_config  # noqa: E402  (рубильники совета: rank_ideas, ask_llm, orchestra)
import direction  # noqa: E402  (руль темы: чтение/запись cyborg/data/direction.json)
import feeds  # noqa: E402  (ленты-источник: какие ленты включены, тумблеры пульта, cyborg/data/feeds.json)
import folders  # noqa: E402  (папки-источник: чтение/запись cyborg/data/folders.json)
import keychain  # noqa: E402  (живой состав цепочки для шапки: id'ы плеч, БЕЗ значений ключей)

# ВАЖНО: порядок критичен. wiring при импорте кладёт idea_engine/ в sys.path, поэтому
# rejected (живёт в idea_engine/) и collect_source (organs/ — тоже idea_engine/) импортируем
# ПОСЛЕ wiring. Ruff I001 сортирует по алфавиту и ломает этот порядок — поэтому noqa на каждой
# строке И isort-sorting отключена здесь вручную (нарушение умышленное, не переупорядочивать).
from wiring import build_organs  # noqa: E402  (метаданные органов; импорт чистый)
import rejected  # noqa: E402  (счётчик отклонённых для пульта; idea_engine на path через wiring)
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
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
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
                encoding="utf-8",
                errors="replace",
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


def _start_observe():
    """Наблюдательный обход органа-источника по кнопке — печатает от первого лица
    (зашёл в паблик → прочитал пост → подумал) в тот же живой вывод, что и прогоны.
    Read-only: зовёт орган-источник как есть, в копилку ничего не пишет."""
    return _start_proc("наблюдаю за источниками", ["observe_sources.py"])


# --- автономный режим (рубильник): фон гоняет ТОТ ЖЕ сбор по таймеру ---
# AUTO_FILE объявлен выше в блоке констант (= config.AUTO_JSON).
_AUTO = {"last": 0.0}
_AUTO_MIN, _AUTO_MAX = 5, 240  # границы интервала, мин


def _load_auto():
    try:
        with open(AUTO_FILE, encoding="utf-8") as f:
            d = json.load(f)
        iv = int(d.get("interval_min", 30))
        return {"on": bool(d.get("on")), "interval_min": max(_AUTO_MIN, min(iv, _AUTO_MAX))}
    except Exception:
        return {"on": False, "interval_min": 30}


def _save_auto(on, interval_min):
    iv = max(_AUTO_MIN, min(int(interval_min), _AUTO_MAX))
    tmp = AUTO_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
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
    if time.time() - _AUTO["last"] < st["interval_min"] * 60:
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
        time.sleep(30)
        try:
            _auto_tick()
        except Exception as e:
            print(f"[auto_loop] сбой тика (продолжаю): {type(e).__name__}: {e}", flush=True)


def _set_idea(idea_id, status):
    """Разбор идеи — через канонический CLI idea_engine (он же перерисует inbox.md)."""
    if status not in ("take", "later", "trash"):
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
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, "run.py", "status", str(int(idea_id)), status],
        cwd=IDEA,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    out = (p.stdout or "").strip() + (p.stderr or "").strip()
    return {"ok": p.returncode == 0 and "NOT_FOUND" not in out, "msg": out[:200]}


_RUN_LINE = re.compile(r"^- \[(?P<ts>[^\]]+)\] «(?P<goal>.*?)» → (?P<chain>.*?) \| (?P<res>.*)$")


def _read_runs():
    runs = []
    try:
        with open(CYBORG + "/data/runs.md", encoding="utf-8") as f:
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
    path = os.path.join(CYBORG, "data", "source_status.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


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
        with open(config.IE_STATE_JSON, encoding="utf-8") as f:
            json.load(f)
    except Exception as e:
        state_err = str(e)[:200]
    # Источники: per-source статус из source_status.json (если есть). Упавший = есть error.
    sources = _read_source_status()
    src_down = []
    if isinstance(sources, dict):
        for name, st in (sources.get("sources") or {}).items():
            if isinstance(st, dict) and st.get("error"):
                src_down.append(name)
    ok = bool(llm_ok and state_err is None and not src_down)
    return {
        "ok": ok,
        "llm": {"available": llm_ok},
        "state_json": {"ok": state_err is None, "error": state_err},
        "sources": {"down": src_down, "status": sources},
        "last_run": {"rc": RUN.get("rc"), "running": RUN.get("running")},
    }


def _read_inbox():
    try:
        with open(IDEA + "/data/state.json", encoding="utf-8") as f:
            s = json.load(f)
        return {
            "cap": s.get("cap", 0),
            "tick": s.get("tick", 0),
            "ideas": s.get("ideas", []),
            "finish": s.get("finish"),
            "seen_count": len(s.get("seen", [])),
        }
    except Exception as e:
        return {"error": str(e)[:200], "cap": 0, "tick": 0, "ideas": [], "finish": None, "seen_count": 0}


def _read_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
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
                    "purpose": (c.get("purpose") or "")[:220],
                    "needs_keys": c.get("needs_keys") or [],
                    "language": c.get("language", ""),
                }
            )
        return {"total": len(cards), "by_status": by_status, "by_project": by_project, "cards": slim}
    except Exception as e:
        return {"error": str(e)[:200], "total": 0, "by_status": {}, "by_project": {}, "cards": []}


def _read_lab():
    try:
        with open(LAB_ROUTER, encoding="utf-8") as f:
            r = json.load(f)
        feats = [
            {
                "slug": x.get("slug"),
                "title": x.get("title"),
                "status": x.get("status"),
                "reviewed": bool(x.get("reviewed")),
                "enabled": bool(x.get("enabled")),
                "why": (x.get("why") or "")[:300],
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
    ключа (keychain.build_chain даёт только те плечи, чей ключ непуст). Раньше отдавали статичный
    ask_llm._MODEL — при отсутствии ключа бейдж врал про плечи, которых нет (аудит 2026-07-17).
    Печатаем ТОЛЬКО id плеч (не model/apiKey/baseUrl) — секрет не утечёт."""
    chain = keychain.build_chain()
    return {"present": bool(chain), "model": "→".join(c["id"] for c in chain) or ask_llm._MODEL}


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
        "now": time.strftime("%H:%M:%S"),
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
        "rejected": rejected.count(),  # сколько идей отклонено «мусором» (учат генератор/судью)
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # тихий сервер
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, f"index.html не читается: {e}", "text/plain; charset=utf-8")
        elif self.path == "/bodies.js":
            try:
                with open(os.path.join(HERE, "bodies.js"), encoding="utf-8") as f:
                    self._send(200, f.read(), "text/javascript; charset=utf-8")
            except Exception as e:
                self._send(500, f"// bodies.js: {e}", "text/javascript; charset=utf-8")
        elif self.path == "/api/state":
            try:
                self._json(_api_state())
            except Exception as e:
                self._json({"error": str(e)[:300]}, 500)
        elif self.path == "/api/run":
            try:
                with _LOCK:
                    self._json({k: RUN[k] for k in ("running", "goal", "lines", "rc")})
            except Exception as e:
                self._json({"error": str(e)[:300]}, 500)
        elif self.path == "/api/folders/probe":
            # проба текущих папок при загрузке пульта (счётчики не на каждом poll /api/state —
            # обход дорог; отдельный редкий вызов). Валиден ли путь + сколько в нём файлов.
            try:  # проба ВСЕХ папок (вкл+выкл) — счётчик файлов виден
                self._json({"probe": collect_source.probe_paths(folders.all_paths())})
            except Exception as e:
                self._json({"error": str(e)[:300]}, 500)
        elif self.path == "/api/health":
            # healthcheck: статус ключевых компонентов для мониторинга/алертинга.
            # ok=True когда LLM-цепочка жива, state.json парсится, и НИ ОДИН источник не упал
            # (источник с error в source_status.json = явный сбой сети/кред — виден в /health).
            try:
                self._json(_health())
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:300]}, 500)
        else:
            self._json({"error": "нет такого пути"}, 404)

    def do_POST(self):
        # анти-CSRF: чужой сайт в браузере юзера не должен дёргать наши действия.
        # Браузер на POST шлёт Origin — принимаем только свой (или его отсутствие: curl/скрипты).
        origin = self.headers.get("Origin", "")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            self._json({"ok": False, "msg": "чужой источник — отказано"}, 403)
            return
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "application/json" not in ctype:  # form-POST с text/plain сюда не пройдёт
            self._json({"ok": False, "msg": "нужен Content-Type: application/json"}, 415)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            self._json({"ok": False, "msg": "плохой JSON"}, 400)
            return
        if self.path == "/api/run":
            goal = str(body.get("goal") or "").replace("\n", " ").strip()[:200]
            if not goal:
                self._json({"ok": False, "msg": "пустая цель"}, 400)
                return
            ok = _start_run(goal)
            self._json({"ok": ok, "msg": "" if ok else "прогон уже идёт"})
        elif self.path == "/api/observe":
            ok = _start_observe()
            self._json({"ok": ok, "msg": "" if ok else "прогон уже идёт"})
        elif self.path == "/api/auto":
            try:
                iv = int(body.get("interval_min", 30))
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
                res = {"ok": False, "msg": ("не вышло: " + str(e))[:200]}
            self._json(res)
        else:
            self._json({"error": "нет такого пути"}, 404)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=_auto_loop, daemon=True).start()  # фон-рубильник (по умолчанию выключен)
    print(f"Пульт киборга: http://127.0.0.1:{PORT}  (Ctrl+C — стоп)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
