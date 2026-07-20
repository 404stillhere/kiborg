"""Тесты сервера пульта (panel/serve.py) — чистые хелперы.

Прицел — не UI, а места, где сервер трогает диск и чужой ввод:
  1. _read_runs — парсинг файла журнала прогонов, устойчивость к мусору/отсутствию.
  2. _set_idea — гейт статуса ДО subprocess (никаких сторонних значений в CLI).
Пишем во временные папки через монкипатч глобалей serve.* — реальные файлы пульта не трогаем.
Только stdlib. Запуск: cd panel && python -m unittest discover -s tests -p "test_*.py"
"""

import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # panel/
sys.path.insert(0, BASE)

import serve  # noqa: E402


class TestReadRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_rn_")
        os.makedirs(os.path.join(self.tmp, "data"))
        self._orig = serve.CYBORG
        serve.CYBORG = self.tmp

    def tearDown(self):
        serve.CYBORG = self._orig

    def test_parses_real_line(self):
        p = os.path.join(self.tmp, "data", "runs.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# журнал\n")
            f.write(
                "- [2026-07-11 11:52:34] «приноси свежие идеи» → " "collect_source -> ideate -> deliver | delivered=3\n"
            )
        runs = serve._read_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["goal"], "приноси свежие идеи")
        self.assertEqual(runs[0]["chain"], ["collect_source", "ideate", "deliver"])
        self.assertEqual(runs[0]["deliverable"], "delivered")
        self.assertEqual(runs[0]["value"], "3")
        self.assertIsNone(runs[0]["degraded"])  # без хвоста ⚠ = None
        self.assertIsNone(runs[0]["council"])  # без хвоста совет = None

    def test_parses_degraded_tail(self):
        # незакоммиченная правка: _read_runs парсит хвост « | ⚠ <flag>» в поле degraded
        # (то, что harvest._log пишет через _degrade_note → пульт показывает деградацию).
        p = os.path.join(self.tmp, "data", "runs.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(
                "- [2026-07-11 11:52:34] «приноси свежие идеи» → "
                "collect_source -> deliver | delivered=1 | ⚠ stub-отсеяно=2 · дубликатов=1\n"
            )
        runs = serve._read_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["deliverable"], "delivered")  # ⚠ отрезан, key=val цел
        self.assertEqual(runs[0]["value"], "1")
        self.assertEqual(runs[0]["degraded"], "stub-отсеяно=2 · дубликатов=1")

    def test_parses_degraded_and_council_tails(self):
        # оба хвоста в ОДНОЙ строке. Прод-порядок (harvest._log:317-321): совет ПЕРВЫМ,
        # потом ⚠. Парсер должен корректно разделить оба, не склеив совет в degraded.
        p = os.path.join(self.tmp, "data", "runs.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(
                "- [2026-07-11 11:52:34] «приноси свежие идеи» → ideate | ideas=2 "
                "| совет: оркестр ПРОСНУЛСЯ | ⚠ источник в фолбэке\n"
            )
        runs = serve._read_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["value"], "2")
        self.assertEqual(runs[0]["council"], "оркестр ПРОСНУЛСЯ")
        self.assertEqual(runs[0]["degraded"], "источник в фолбэке")

    def test_missing_file_safe(self):
        self.assertEqual(serve._read_runs(), [])


class TestReadSourceStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_src_")
        os.makedirs(os.path.join(self.tmp, "data"))
        self._orig = serve.CYBORG
        serve.CYBORG = self.tmp

    def tearDown(self):
        serve.CYBORG = self._orig

    def test_reads_status(self):
        p = os.path.join(self.tmp, "data", "source_status.json")
        payload = {
            "checked_at": "2026-07-12 20:01:58",
            "degraded": False,
            "sources": {
                "hn": {"items": 6, "ok": True, "error": None},
                "reddit": {"items": 0, "ok": False, "error": "reddit: 403"},
            },
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        r = serve._read_source_status()
        self.assertFalse(r["sources"]["reddit"]["ok"])
        self.assertEqual(r["sources"]["hn"]["items"], 6)

    def test_missing_file_none(self):
        # файла ещё нет (harvest не гоняли) -> None, пульт просто не рисует строку
        self.assertIsNone(serve._read_source_status())


class TestSetIdeaGate(unittest.TestCase):
    def test_bad_status_rejected_before_subprocess(self):
        # статус вне take|later|trash отбивается ДО запуска CLI (сторонним значениям хода нет)
        r = serve._set_idea(1, "; rm -rf")
        self.assertFalse(r["ok"])
        self.assertIn("take|later|trash", r["msg"])

    def test_triage_deferred_while_run_active(self):
        # идёт прогон (deliver пишет state.json) -> триаж НЕ мутирует файл параллельно
        # (lost-update): отбивается ДО subprocess с флагом busy
        orig_running = serve.RUN["running"]
        orig_sub = serve.subprocess.run

        def _boom(*a, **k):
            raise AssertionError("subprocess не должен вызываться при активном прогоне")

        serve.RUN["running"] = True
        serve.subprocess.run = _boom
        try:
            r = serve._set_idea(1, "trash")
            self.assertFalse(r["ok"])
            self.assertTrue(r.get("busy"))
        finally:
            serve.subprocess.run = orig_sub
            serve.RUN["running"] = orig_running


class TestStopRun(unittest.TestCase):
    """Кнопка «стоп» рядом с кнопками активности — убивает текущий подпроцесс."""

    def setUp(self):
        self._orig_run = dict(serve.RUN)
        self._orig_proc = dict(serve._PROC)

    def tearDown(self):
        serve.RUN.clear()
        serve.RUN.update(self._orig_run)
        serve._PROC.clear()
        serve._PROC.update(self._orig_proc)

    def test_kills_running_process(self):
        killed = []

        class FakeProc:
            def poll(self):
                return None  # ещё работает

            def kill(self):
                killed.append(1)

        serve.RUN.update(running=True, lines=[])
        serve._PROC["p"] = FakeProc()
        ok = serve._stop_run()
        self.assertTrue(ok)
        self.assertEqual(killed, [1])
        self.assertIn("остановлено", serve.RUN["lines"][-1])

    def test_noop_when_nothing_running(self):
        serve.RUN.update(running=False)
        serve._PROC["p"] = None
        self.assertFalse(serve._stop_run())

    def test_noop_when_proc_already_finished(self):
        class FakeProc:
            def poll(self):
                return 0  # уже завершился сам

            def kill(self):
                raise AssertionError("не должен звать kill на уже завершённом процессе")

        serve.RUN.update(running=True, lines=[])
        serve._PROC["p"] = FakeProc()
        self.assertFalse(serve._stop_run())


class TestAutoConfig(unittest.TestCase):
    """_load_auto/_save_auto — конфиг автономности: clamp интервала в [_AUTO_MIN,_AUTO_MAX],
    дефолт на битом/отсутствующем файле, атомарная запись (os.replace). Раньше не покрыто."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_auto_")
        self.f = os.path.join(self.tmp, "auto.json")
        self._orig = serve.AUTO_FILE
        serve.AUTO_FILE = self.f

    def tearDown(self):
        serve.AUTO_FILE = self._orig

    def test_save_load_roundtrip(self):
        serve._save_auto(True, 60)
        self.assertEqual(serve._load_auto(), {"on": True, "interval_min": 60})

    def test_save_clamps_interval_high_and_low(self):
        serve._save_auto(True, 9999)
        self.assertEqual(serve._load_auto()["interval_min"], serve._AUTO_MAX)  # верх -> 240
        serve._save_auto(True, 1)
        self.assertEqual(serve._load_auto()["interval_min"], serve._AUTO_MIN)  # низ -> 5

    def test_load_defaults_when_file_missing(self):
        self.assertEqual(serve._load_auto(), {"on": False, "interval_min": 30})  # нет файла -> off/30

    def test_load_defaults_on_corrupt_json(self):
        with open(self.f, "w", encoding="utf-8") as fh:
            fh.write("{битый json")
        self.assertEqual(serve._load_auto(), {"on": False, "interval_min": 30})

    def test_load_clamps_stored_out_of_range(self):
        with open(self.f, "w", encoding="utf-8") as fh:
            json.dump({"on": True, "interval_min": 9999}, fh)
        self.assertEqual(serve._load_auto()["interval_min"], serve._AUTO_MAX)  # clamp и на чтении

    def test_save_is_atomic_no_tmp_leftover(self):
        serve._save_auto(False, 45)
        self.assertFalse(os.path.exists(self.f + ".tmp"))  # os.replace убрал tmp
        self.assertTrue(os.path.exists(self.f))


class TestAutoTick(unittest.TestCase):
    """_auto_tick — один тик авто-петли (вынесен из while-True ради тестируемости 2026-07-15):
    автосбор запускается ТОЛЬКО если автономность вкл + пора по интервалу + прогон не идёт.
    Резилиенс петли (сбой тика не валит поток-демон) держится на этой развязке + try/except в _auto_loop."""

    def setUp(self):
        self._orig = (dict(serve.RUN), dict(serve._AUTO), serve._load_auto, serve._start_proc)
        self.started = []
        serve._start_proc = lambda *a, **k: (self.started.append(a) or True)

    def tearDown(self):
        run, auto, load, start = self._orig
        serve.RUN.clear()
        serve.RUN.update(run)
        serve._AUTO.clear()
        serve._AUTO.update(auto)
        serve._load_auto = load
        serve._start_proc = start

    def test_fires_when_on_due_and_idle(self):
        serve._load_auto = lambda: {"on": True, "interval_min": 30}
        serve._AUTO["last"] = 0.0  # давно → пора
        serve.RUN["running"] = False
        self.assertTrue(serve._auto_tick())  # запустил
        self.assertEqual(len(self.started), 1)

    def test_skips_when_off(self):
        serve._load_auto = lambda: {"on": False, "interval_min": 30}
        serve._AUTO["last"] = 0.0
        serve.RUN["running"] = False
        self.assertFalse(serve._auto_tick())
        self.assertEqual(self.started, [])

    def test_skips_when_not_due(self):
        serve._load_auto = lambda: {"on": True, "interval_min": 30}
        serve._AUTO["last"] = serve.time.time()  # только что → ещё не пора
        serve.RUN["running"] = False
        self.assertFalse(serve._auto_tick())
        self.assertEqual(self.started, [])

    def test_skips_when_busy(self):
        serve._load_auto = lambda: {"on": True, "interval_min": 30}
        serve._AUTO["last"] = 0.0
        serve.RUN["running"] = True  # прогон идёт → второй не запускаем
        self.assertFalse(serve._auto_tick())
        self.assertEqual(self.started, [])


class TestReadLab(unittest.TestCase):
    """Витрина фабрики фич в /api/state. `.feature-lab/` СНЕСЁН юзером (2026-07-14) → _read_lab
    теперь всегда бьёт в ветку «нет файла»: страж, что пульт это переживает (exists:False, не падает).
    Плюс пиним логику замка (ready+unreviewed=locked) — вернётся б-3 с песочницей, поведение цело."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_lab_")
        self.f = os.path.join(self.tmp, "router.json")
        self._orig = serve.LAB_ROUTER
        serve.LAB_ROUTER = self.f

    def tearDown(self):
        serve.LAB_ROUTER = self._orig

    def test_missing_file_absent_not_crash(self):
        # текущее РЕАЛЬНОЕ состояние: .feature-lab снесён → файла нет → витрина пуста, без краха
        self.assertEqual(serve._read_lab(), {"exists": False, "locked": False, "features": [], "needs_manual": 0})

    def test_corrupt_json_safe(self):
        with open(self.f, "w", encoding="utf-8") as fh:
            fh.write("{битый json")
        self.assertFalse(serve._read_lab()["exists"])  # битый роутер не роняет /api/state

    def test_ready_unreviewed_is_locked(self):
        with open(self.f, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "features": [
                        {"slug": "f1", "title": "T", "status": "ready", "reviewed": False, "enabled": False, "why": "w"}
                    ]
                },
                fh,
            )
        lab = serve._read_lab()
        self.assertTrue(lab["exists"])
        self.assertTrue(lab["locked"])  # готовая непроверенная фича = замок петли
        self.assertEqual(lab["features"][0]["slug"], "f1")

    def test_reviewed_not_locked(self):
        with open(self.f, "w", encoding="utf-8") as fh:
            json.dump(
                {"features": [{"slug": "f1", "status": "ready", "reviewed": True}], "needs_manual": ["x", "y"]}, fh
            )
        lab = serve._read_lab()
        self.assertFalse(lab["locked"])  # проверена → замка нет
        self.assertEqual(lab["needs_manual"], 2)


class TestKeyState(unittest.TestCase):
    """_key_state — шапка показывает ЖИВОЙ состав цепочки (id плеч), а не статичный ярлык.
    Регресс-страж против бага 2026-07-17: при одном ключе бейдж врал «gemini→muse (hybrid)»."""

    def setUp(self):
        self._orig = serve.keychain.build_chain

    def tearDown(self):
        serve.keychain.build_chain = self._orig

    def test_both_arms_present(self):
        serve.keychain.build_chain = lambda *a, **k: [
            {"id": "gemini", "model": "x"},
            {"id": "muse-spark", "model": "y"},
        ]
        st = serve._key_state()
        self.assertTrue(st["present"])
        self.assertEqual(st["model"], "gemini→muse-spark")

    def test_single_arm_not_lying_about_second(self):
        # только closerouter-ключ → плечо одно; бейдж НЕ обещает gemini (корень бага)
        serve.keychain.build_chain = lambda *a, **k: [{"id": "muse-spark", "model": "y"}]
        st = serve._key_state()
        self.assertTrue(st["present"])
        self.assertEqual(st["model"], "muse-spark")
        self.assertNotIn("gemini", st["model"])

    def test_no_keys_absent(self):
        serve.keychain.build_chain = lambda *a, **k: []
        st = serve._key_state()
        self.assertFalse(st["present"])  # ключей нет → шапка покажет «нет ключа»

    def test_id_only_no_secret_leak(self):
        # печатаем ТОЛЬКО id плеча, не model/apiKey/baseUrl (защита от утечки ключа в шапку)
        serve.keychain.build_chain = lambda *a, **k: [
            {"id": "gemini", "model": "gemini-2.5-flash-lite", "apiKey": "SECRET", "baseUrl": "u"}
        ]
        st = serve._key_state()
        self.assertNotIn("SECRET", st["model"])
        self.assertNotIn("gemini-2.5-flash-lite", st["model"])  # model-строка не в бейдже
        self.assertEqual(st["model"], "gemini")


if __name__ == "__main__":
    unittest.main(verbosity=2)
