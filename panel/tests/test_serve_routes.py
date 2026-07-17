"""HTTP-тесты роутов пульта (serve.Handler) через реальный сервер на эфемерном порту.

Раньше покрыты были только ХЕЛПЕРЫ (_read_runs/...), а сами POST-роуты и их
валидация — нет. Тут проверяем POST /api/folders, /api/direction, /api/feeds (добавлены под
фичи направление/папки/тумблеры-лент) + общие гейты do_POST: Content-Type (415), битый JSON
(400), тип тела (400). folders/direction/feeds пишут в temp (реальные data/*.json не трогаем)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import serve  # noqa: E402


class TestServeRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # свой сервер на 127.0.0.1:0 (эфемерный порт) — не конфликтует с живым пультом на 8737
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()        # закрыть слушающий сокет (shutdown лишь останавливает serve_forever)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_routes_")
        # все пишущие роуты уводим в temp — реальные конфиги/раскладку/авто не трогаем
        self._saved = {"fp": serve.folders.PATH, "dp": serve.direction.PATH,
                       "auto": serve.AUTO_FILE, "feeds": serve.feeds.PATH}
        serve.folders.PATH = os.path.join(self.tmp, "folders.json")
        serve.direction.PATH = os.path.join(self.tmp, "direction.json")
        serve.AUTO_FILE = os.path.join(self.tmp, "auto.json")
        serve.feeds.PATH = os.path.join(self.tmp, "feeds.json")

    def tearDown(self):
        serve.folders.PATH = self._saved["fp"]
        serve.direction.PATH = self._saved["dp"]
        serve.AUTO_FILE = self._saved["auto"]
        serve.feeds.PATH = self._saved["feeds"]

    def _post(self, path, body=None, ctype="application/json", raw=None):
        data = raw if raw is not None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": ctype}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_folders_valid_saves_and_normalizes(self):
        code, body = self._post("/api/folders", {"paths": ["M:/x", "M:\\x", "C:/y/"]})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["paths"], ["M:/x", "C:/y"])   # дедуп (M:\x==M:/x) + снят хвостовой /

    def test_folders_non_list_rejected(self):
        code, body = self._post("/api/folders", {"paths": "не список"})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])

    def test_folders_post_returns_probe(self):
        # ответ на сохранение папок несёт пробу: путь валиден? сколько текстовых файлов?
        d = tempfile.mkdtemp(prefix="probe_post_")
        try:
            with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as f:
                f.write('"""a."""\n')
            code, body = self._post("/api/folders", {"paths": [d]})
            self.assertEqual(code, 200)
            self.assertIn("probe", body)
            saved = body["paths"][0]                          # сервер нормализует путь — берём его
            self.assertTrue(body["probe"][saved]["exists"])
            self.assertGreaterEqual(body["probe"][saved]["files"], 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_folders_probe_get_reads_current(self):
        d = tempfile.mkdtemp(prefix="probe_get_")
        try:
            with open(os.path.join(d, "b.md"), "w", encoding="utf-8") as f:
                f.write("# b\n")
            self._post("/api/folders", {"paths": [d]})       # сохранили в temp folders.json
            code, body = self._get("/api/folders/probe")
            self.assertEqual(code, 200)
            self.assertIn("probe", body)
            self.assertTrue(any(v["exists"] and v["files"] >= 1 for v in body["probe"].values()))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_feeds_valid_saves_and_canonicalizes(self):
        # тумблеры лент: произвольный порядок + дубль + неизвестное → канон-порядок, только известные
        code, body = self._post("/api/feeds", {"enabled": ["telegram", "hn", "telegram", "myspace"]})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["enabled"], ["hn", "telegram"])   # канон-порядок ALL_FEEDS, дедуп, мусор снят
        self.assertEqual(body["all"], serve.feeds.ALL_FEEDS)

    def test_feeds_empty_all_off(self):
        code, body = self._post("/api/feeds", {"enabled": []})   # все ленты выключены — законно
        self.assertEqual(code, 200)
        self.assertEqual(body["enabled"], [])

    def test_feeds_non_list_rejected(self):
        code, body = self._post("/api/feeds", {"enabled": "hn"})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])

    def test_direction_valid_saves(self):
        code, body = self._post("/api/direction", {"current": "железки"})
        self.assertEqual(code, 200)
        self.assertEqual(body["current"], "железки")

    def test_direction_wrong_type_rejected(self):
        code, body = self._post("/api/direction", {"current": 123})
        self.assertEqual(code, 400)

    def test_bad_json_rejected(self):
        code, body = self._post("/api/folders", raw=b"{not json")
        self.assertEqual(code, 400)

    def test_non_json_content_type_rejected(self):
        code, body = self._post("/api/folders", ctype="text/plain", raw=b"paths=1")
        self.assertEqual(code, 415)

    def test_unknown_route_404(self):
        code, body = self._post("/api/nope", {})
        self.assertEqual(code, 404)

    def test_idea_bad_id_rejected(self):
        code, body = self._post("/api/idea", {"status": "take"})     # без id → не доходит до подпроцесса
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])

    def test_auto_saves_and_clamps_interval(self):
        code, body = self._post("/api/auto", {"on": True, "interval_min": 9999})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["interval_min"], serve._AUTO_MAX)      # 9999 обрезан до потолка
        self.assertTrue(body["on"])

    def test_auto_bad_interval_rejected(self):
        # РЕГРЕССИЯ (нашла фабрика б-3 2026-07-15): единственный POST-роут без type-guard —
        # {"interval_min":"abc"} бросал ValueError в _save_auto (int()) ВНЕ try do_POST → обрыв
        # запроса. Теперь как соседние роуты: кривой тип → 400, сервер жив.
        code, body = self._post("/api/auto", {"on": True, "interval_min": "abc"})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])

    def test_foreign_origin_rejected_csrf(self):
        # анти-CSRF гейт (тест-страж от фабрики б-3 2026-07-15): POST с ЧУЖИМ Origin (другой сайт,
        # открытый в браузере юзера, дёргает наш локальный пульт) → 403. Гейт в do_POST был, но без
        # теста. Свой Origin / его отсутствие (curl/скрипты) проходят — проверено остальными тестами.
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/feeds",
            data=json.dumps({"enabled": []}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "http://evil.example.com"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code, resp = r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            code, resp = e.code, json.loads(e.read().decode("utf-8"))
        self.assertEqual(code, 403)
        self.assertFalse(resp["ok"])

    def test_stop_nothing_to_stop(self):
        code, body = self._post("/api/stop", {})                     # прогон не идёт → нечего останавливать
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"])

    def test_run_valid_goal_starts(self):
        # спавн подпроцесса мокаем — проверяем валидацию+проброс цели, без реального прогона
        orig = serve._start_run
        got = {}
        serve._start_run = lambda goal: (got.__setitem__("goal", goal), True)[1]
        try:
            code, body = self._post("/api/run", {"goal": "принеси идеи\nвторая строка"})
        finally:
            serve._start_run = orig
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(got["goal"], "принеси идеи вторая строка")   # \n схлопнут, обрезано до 200

    def test_run_empty_goal_rejected(self):
        orig = serve._start_run
        serve._start_run = lambda goal: True
        try:
            code, body = self._post("/api/run", {"goal": "   "})      # пусто после strip → 400
        finally:
            serve._start_run = orig
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])

    def test_observe_starts(self):
        orig = serve._start_observe
        serve._start_observe = lambda: True
        try:
            code, body = self._post("/api/observe", {})
        finally:
            serve._start_observe = orig
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_run_get_returns_state(self):
        # GET /api/run — состояние прогона (happy path). RUN по умолчанию в покое.
        code, body = self._get("/api/run")
        self.assertEqual(code, 200)
        self.assertIn("running", body)
        self.assertIn("goal", body)
        self.assertIn("rc", body)

    def test_run_get_error_returns_500_json(self):
        # error_gap (закрыт): при падении чтения RUN — 500 с JSON-телом, как у соседних GET-роутов
        # /api/state и /api/folders/probe (try/except). Раньше /api/run был единственный GET без
        # error-ветки → необработанный трейсбек в лог, пульту пустой 500.
        orig_run = serve.RUN
        class _Boom(dict):
            def __getitem__(self, k):
                raise RuntimeError("RUN порчен")
        serve.RUN = _Boom()
        try:
            code, body = self._get("/api/run")
        finally:
            serve.RUN = orig_run
        self.assertEqual(code, 500)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
