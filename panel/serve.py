"""Пульт киборга — локальный веб-интерфейс: что киборг умеет и что происходит внутри.

Только чтение состояния + два действия, что и так есть в CLI:
  - разобрать идею (take/later/trash) — через idea_engine/run.py status
  - запустить прогон — через cyborg/run.py "<цель>" (вывод стримится в браузер)

Только stdlib, без venv. Слушает ТОЛЬКО 127.0.0.1. Ключ Gemini не читает —
проверяет лишь его наличие через ask_llm.available().

Запуск:  python M:/projects/kiborg/panel/serve.py   →  http://127.0.0.1:8737
"""
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

ROOT = "M:/projects/kiborg"
CYBORG = ROOT + "/cyborg"
IDEA = ROOT + "/idea_engine"
REGISTRY = "M:/projects/_shared/organs.json"
LAB_ROUTER = ROOT + "/.feature-lab/router.json"
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8737

if CYBORG not in sys.path:
    sys.path.insert(0, CYBORG)

import ask_llm  # noqa: E402  (только available() — ключ не читаем и не показываем)
from wiring import build_organs  # noqa: E402  (метаданные органов; импорт чистый)

_ORGANS = build_organs()

# --- текущий прогон (один за раз) ---
RUN = {"running": False, "goal": None, "lines": [], "rc": None, "started": 0.0}
_LOCK = threading.Lock()
_PROC = {"p": None}  # текущий Popen (не сериализуем в JSON — держим отдельно от RUN)
RUN_TIMEOUT = 180  # с; нормальный прогон < 40с. Дольше — что-то зависло (медленная сеть):
                   # убиваем подпроцесс, чтобы кнопка/пульт не залипли в «работает…» навсегда.


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
                [sys.executable, *args], cwd=CYBORG,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", env=env,
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


def _start_harvest():
    """Разовый сбор в копилку по кнопке (то же, что CLI `python harvest.py 1 --force`).
    --force: ручной клик перебивает гейт «лента не менялась» — юзер просит собрать сейчас."""
    return _start_proc("сбор свежих идей в копилку", ["harvest.py", "1", "--force"])


def _start_observe():
    """Наблюдательный обход органа-источника по кнопке — печатает от первого лица
    (зашёл в паблик → прочитал пост → подумал) в тот же живой вывод, что и прогоны.
    Read-only: зовёт орган-источник как есть, в копилку ничего не пишет."""
    return _start_proc("наблюдаю за источниками", ["observe_sources.py"])


def _set_idea(idea_id, status):
    """Разбор идеи — через канонический CLI idea_engine (он же перерисует inbox.md)."""
    if status not in ("take", "later", "trash"):
        return {"ok": False, "msg": "статус должен быть take|later|trash"}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, "run.py", "status", str(int(idea_id)), status],
        cwd=IDEA, capture_output=True, encoding="utf-8", errors="replace", env=env,
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
                key, _, val = res.partition("=")
                runs.append({"ts": m.group("ts"), "goal": m.group("goal"),
                             "chain": [s.strip() for s in m.group("chain").split("->")],
                             "deliverable": key, "value": val})
    except Exception:
        pass
    return runs


def _read_stash():
    """Копилка автономных прогонов (cyborg/data/idea_stash.jsonl) — count + свежие."""
    path = os.path.join(CYBORG, "data", "idea_stash.jsonl")
    ideas = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ideas.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    latest = list(reversed(ideas))[:8]  # свежие сверху, показываем до 8
    slim = [{"title": d.get("title", ""), "why": (d.get("why") or "")[:280],
             "effort": d.get("effort", ""), "brain": d.get("brain", ""),
             "stashed_at": d.get("stashed_at", "")} for d in latest]
    return {"total": len(ideas), "latest": slim}


def _read_source_status():
    """Живой per-source статус (cyborg/data/source_status.json) — пишется harvest'ом на
    каждом авто-прогоне (не-force). Нет файла (ещё не гоняли) -> None, пульт не показывает."""
    path = os.path.join(CYBORG, "data", "source_status.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_inbox():
    try:
        with open(IDEA + "/data/state.json", encoding="utf-8") as f:
            s = json.load(f)
        return {"cap": s.get("cap", 3), "tick": s.get("tick", 0),
                "ideas": s.get("ideas", []), "finish": s.get("finish"),
                "seen_count": len(s.get("seen", []))}
    except Exception as e:
        return {"error": str(e)[:200], "cap": 3, "tick": 0, "ideas": [], "finish": None, "seen_count": 0}


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
            slim.append({"name": c.get("name"), "project": pr, "status": st,
                         "purpose": (c.get("purpose") or "")[:220],
                         "needs_keys": c.get("needs_keys") or [],
                         "language": c.get("language", "")})
        return {"total": len(cards), "by_status": by_status,
                "by_project": by_project, "cards": slim}
    except Exception as e:
        return {"error": str(e)[:200], "total": 0, "by_status": {}, "by_project": {}, "cards": []}


def _read_lab():
    try:
        with open(LAB_ROUTER, encoding="utf-8") as f:
            r = json.load(f)
        feats = [{"slug": x.get("slug"), "title": x.get("title"),
                  "status": x.get("status"), "reviewed": bool(x.get("reviewed")),
                  "enabled": bool(x.get("enabled")), "why": (x.get("why") or "")[:300]}
                 for x in r.get("features", [])]
        locked = any(f["status"] == "ready" and not f["reviewed"] for f in feats)
        return {"exists": True, "locked": locked, "features": feats,
                "needs_manual": len(r.get("needs_manual", []))}
    except Exception:
        return {"exists": False, "locked": False, "features": [], "needs_manual": 0}


LAYOUT_FILE = os.path.join(HERE, "layout.json")


def _read_layout():
    """Раскладка органов на каркасе (конструктор юзера). Нет файла = все в лотке."""
    try:
        with open(LAYOUT_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


_LAYOUT_MAX_KEYS = 64  # органов немного; больше — мусор/раздувание, режем


def _num(v):  # число-координата, но НЕ bool (isinstance(True, int) == True в питоне)
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _save_layout(lay):
    clean = {}
    for k, v in lay.items():
        if len(clean) >= _LAYOUT_MAX_KEYS:
            break
        if not isinstance(k, str) or not k or len(k) > 40:
            continue
        if isinstance(v, dict) and _num(v.get("x")) and _num(v.get("y")):
            clean[k] = {"x": round(float(v["x"]), 1), "y": round(float(v["y"]), 1)}
    tmp = LAYOUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LAYOUT_FILE)  # атомарно: обрыв записи не бьёт существующую раскладку


def _api_state():
    wired = [{"name": o.name, "purpose": o.purpose, "role": o.role,
              "produces": o.produces, "consumes": o.consumes,
              "tags": o.tags, "needs": o.needs} for o in _ORGANS]
    return {
        "now": time.strftime("%H:%M:%S"),
        "key": {"present": ask_llm.available(), "model": ask_llm._MODEL},
        "organs": wired,
        "inbox": _read_inbox(),
        "stash": _read_stash(),
        "sources": _read_source_status(),
        "runs": _read_runs(),
        "registry": _read_registry(),
        "lab": _read_lab(),
        "layout": _read_layout(),
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
            with _LOCK:
                self._json({k: RUN[k] for k in ("running", "goal", "lines", "rc")})
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
        elif self.path == "/api/harvest":
            ok = _start_harvest()
            self._json({"ok": ok, "msg": "" if ok else "прогон уже идёт"})
        elif self.path == "/api/observe":
            ok = _start_observe()
            self._json({"ok": ok, "msg": "" if ok else "прогон уже идёт"})
        elif self.path == "/api/stop":
            ok = _stop_run()
            self._json({"ok": ok, "msg": "" if ok else "нечего останавливать"})
        elif self.path == "/api/layout":
            lay = body.get("layout")
            if not isinstance(lay, dict):
                self._json({"ok": False, "msg": "нужен объект layout"}, 400)
                return
            try:
                _save_layout(lay)
                self._json({"ok": True, "msg": ""})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)[:200]}, 500)
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
    print(f"Пульт киборга: http://127.0.0.1:{PORT}  (Ctrl+C — стоп)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
