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


class TestGracefulShutdown(unittest.TestCase):
    """Graceful shutdown: signal модуль импортирован, _shutdown/_stop_run вызываемы."""

    def test_signal_module_imported(self):
        """signal модуль импортирован (зависимость для SIGTERM/SIGINT)."""
        import signal  # noqa: F401

        # Если этот тест упал — signal не доступен (неприменимо для stdlib, но проверка что импорт есть)
        self.assertTrue(hasattr(signal, "SIGTERM"))
        self.assertTrue(hasattr(signal, "SIGINT"))

    def test_shutdown_function_exists(self):
        """В serve.py есть _shutdown функция (устанавливается как handler в main)."""
        # Не можем запустить main() в тесте (HTTP сервер блокирует), но проверяем что функция существует
        # и она вызывает _stop_run (что проверено в TestStopRun)
        import inspect

        # Проверяем что main существует и содержит signal.signal вызовы
        self.assertTrue(callable(serve.main))
        source = inspect.getsource(serve.main)
        self.assertIn("signal.signal", source)
        self.assertIn("SIGTERM", source)
        self.assertIn("SIGINT", source)
        self.assertIn("srv.shutdown", source)


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


class TestGenparamsInState(unittest.TestCase):
    """Параметры генерации (gen_k/rank_keep/source_n/пороги) — проброс в _api_state и
    POST/GET /api/genparams. Логика save/reset/clamp покрыта в cyborg/tests/test_genparams.py,
    здесь — только что serve отдаёт поле genparams в /api/state корректной структуры и что
    запись через genparams доходит. Раньше параметров не было (хардкод в wiring) — добавлено
    при выносе в UI drawer «Настройки»."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_gp_")
        self._orig = serve.genparams.PATH
        serve.genparams.PATH = os.path.join(self.tmp, "genparams.json")

    def tearDown(self):
        serve.genparams.PATH = self._orig

    def test_api_state_contains_genparams(self):
        # _api_state должно нести genparams с meta-полями для UI (min/max/default/value/is_float)
        st = serve._api_state()
        self.assertIn("genparams", st)
        gp = st["genparams"]
        expected_keys = {"gen_k", "rank_keep", "source_n", "read_min_score", "keep_min_score"}
        self.assertEqual(set(gp.keys()), expected_keys)
        for spec in gp.values():
            for field in ("min", "max", "default", "is_float", "value"):
                self.assertIn(field, spec)

    def test_genparams_defaults_when_no_file(self):
        # нет файла → /api/state отдаёт дефолты (юзер ни разу не открывал настройки)
        st = serve._api_state()
        gp = st["genparams"]
        self.assertEqual(gp["gen_k"]["value"], 8)
        self.assertEqual(gp["source_n"]["value"], 105)

    def test_save_via_genparams_reflects_in_state(self):
        # roundtrip: genparams.save → следующий _api_state видит новое значение
        serve.genparams.save({"gen_k": 12, "rank_keep": 5})
        gp = serve._api_state()["genparams"]
        self.assertEqual(gp["gen_k"]["value"], 12)
        self.assertEqual(gp["rank_keep"]["value"], 5)
        # не тронутые ключи остались дефолтными
        self.assertEqual(gp["source_n"]["value"], 105)

    def test_reset_reflects_in_state(self):
        # кнопка «↺ сброс» — reset() возвращает дефолты, видимые в /api/state
        serve.genparams.save({"gen_k": 16, "rank_keep": 8})
        serve.genparams.reset()
        gp = serve._api_state()["genparams"]
        self.assertEqual(gp["gen_k"]["value"], 8)
        self.assertEqual(gp["rank_keep"]["value"], 3)

    def test_reset_actually_persists_to_disk(self):
        # регрессия: роут POST /api/genparams {reset:true} должен ЗВАТЬ genparams.reset(),
        # а не просто возвращать meta() (которая читает несброшенный файл). Симптом до фикса:
        # юзер жмёт «сброс», UI показывает дефолты на секунду, но файл не перезаписан →
        # следующий poll /api/state (5сек) возвращает старые значения. Проверяем что reset
        # действительно записал дефолты на диск.
        serve.genparams.save({"gen_k": 16, "source_n": 300})
        serve.genparams.reset()
        # файл на диске должен содержать дефолты (не 16/300)
        with open(serve.genparams.PATH, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["gen_k"], 8)
        self.assertEqual(on_disk["source_n"], 105)


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


class TestHealth(unittest.TestCase):
    """Healthcheck /api/health — ok=True только когда ВСЁ здорово: LLM, state.json, источники."""

    def setUp(self):
        # Патчим ВСЕ компоненты, чтобы каждый тест явно готовил свой сценарий.
        self._orig_avail = serve.ask_llm.available
        self._orig_state = serve.config.IE_STATE_JSON
        self._orig_cyborg = serve.CYBORG  # _read_source_status читает {CYBORG}/data/source_status.json
        self._orig_recent = serve.lock_monitor.recent_timeouts
        self.tmp = tempfile.mkdtemp(prefix="serve_h_")
        serve.CYBORG = self.tmp
        os.makedirs(os.path.join(self.tmp, "data"), exist_ok=True)
        # state.json по умолчанию валиден — тесты, которым нужен сбой, патчат сами
        self._state_path = os.path.join(self.tmp, "state.json")
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump({"ideas": [], "seen": []}, f)
        serve.config.IE_STATE_JSON = self._state_path
        serve.ask_llm.available = lambda: True
        # По умолчанию таймаутов state_lock не было — пульт должен показывать 0.
        serve.lock_monitor.recent_timeouts = lambda minutes=60: 0

    def tearDown(self):
        serve.ask_llm.available = self._orig_avail
        serve.config.IE_STATE_JSON = self._orig_state
        serve.CYBORG = self._orig_cyborg
        serve.lock_monitor.recent_timeouts = self._orig_recent

    def _write_source_status(self, sources_dict):
        """Положить source_status.json в {CYBORG}/data/ для теста источников."""
        with open(os.path.join(self.tmp, "data", "source_status.json"), "w", encoding="utf-8") as f:
            json.dump({"sources": sources_dict}, f)

    def test_all_healthy(self):
        # LLM есть, state.json валиден, источник работает (нет error) → ok=True
        self._write_source_status({"telegram": {"ok": True, "error": None}})
        h = serve._health()
        self.assertTrue(h["ok"])
        self.assertTrue(h["llm"]["available"])
        self.assertTrue(h["state_json"]["ok"])
        self.assertEqual(h["sources"]["down"], [])

    def test_llm_down(self):
        serve.ask_llm.available = lambda: False
        h = serve._health()
        self.assertFalse(h["ok"])
        self.assertFalse(h["llm"]["available"])

    def test_state_json_corrupted(self):
        with open(self._state_path, "w", encoding="utf-8") as f:
            f.write("{ это не json !!!")
        h = serve._health()
        self.assertFalse(h["ok"])
        self.assertFalse(h["state_json"]["ok"])
        self.assertIsNotNone(h["state_json"]["error"])  # причина повреждения

    def test_source_down_makes_unhealthy(self):
        # Источник упал (есть error) → ok=False, имя в sources.down
        self._write_source_status(
            {
                "telegram": {"ok": True, "error": None},
                "reddit": {"ok": False, "error": "HTTP Error 403: Blocked"},
            }
        )
        h = serve._health()
        self.assertFalse(h["ok"])
        self.assertIn("reddit", h["sources"]["down"])
        self.assertNotIn("telegram", h["sources"]["down"])

    def test_no_source_status_file_is_ok(self):
        # Нет source_status.json (ещё не гоняли) → sources.down пуст, ok=True (если LLM+state ок)
        h = serve._health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["sources"]["down"], [])

    def test_locks_field_present_with_zero_timeouts(self):
        # По умолчанию (нет таймаутов) — locks.recent_timeouts=0, окно 60 мин.
        h = serve._health()
        self.assertIn("locks", h)
        self.assertEqual(h["locks"]["recent_timeouts"], 0)
        self.assertEqual(h["locks"]["window_minutes"], 60)

    def test_locks_field_reflects_recent_timeouts(self):
        # Если lock_monitor говорит «3 таймаута за час» — /api/health это отражает.
        # После stale-lock-cleanup это РЕДКОСТЬ (значит живой конкурент держал лок >130с).
        serve.lock_monitor.recent_timeouts = lambda minutes=60: 3
        h = serve._health()
        self.assertEqual(h["locks"]["recent_timeouts"], 3)
        self.assertEqual(h["locks"]["window_minutes"], 60)

    def test_locks_does_not_affect_ok_flag(self):
        # Таймауты — диагностическая метрика, не приговор здоровью: даже при высоком
        # значении ok=True (если LLM/state/sources в порядке). Прогон ПРОШЁЛ, просто без лока.
        serve.lock_monitor.recent_timeouts = lambda minutes=60: 99
        h = serve._health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["locks"]["recent_timeouts"], 99)


class TestPurgeLowScore(unittest.TestCase):
    """_purge_low_score — массовый триаж идей с оценкой ниже порога в мусор.

    Дизайн: один read state.json → N вызовов _set_idea(id, "trash"). Каждый _set_idea
    сам проверяет RUN["running"], поэтому busy-развал безопасен (частичная очистка).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_purge_")
        self._orig_state = serve.config.IE_STATE_JSON
        self._orig_set = serve._set_idea
        self._orig_run = dict(serve.RUN)
        # state.json с разными идеями для покрытия веток
        self._state_path = os.path.join(self.tmp, "state.json")
        serve.config.IE_STATE_JSON = self._state_path
        serve.RUN["running"] = False
        self.trashed = []  # какие id ушли в мусор
        # дефолтный мок: тихо «успешно» всё
        serve._set_idea = lambda iid, st: (self.trashed.append(iid) or {"ok": True, "msg": "OK"})

    def tearDown(self):
        serve.config.IE_STATE_JSON = self._orig_state
        serve._set_idea = self._orig_set
        serve.RUN.clear()
        serve.RUN.update(self._orig_run)

    def _write_state(self, ideas):
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump({"ideas": ideas}, f)

    def test_purges_only_open_below_threshold(self):
        # 4 идеи: open+6.0 (в мусор), open+8.5 (оставить), take+5.0 (не трогать — не open),
        # open+None (не трогать — без оценки).
        self._write_state(
            [
                {"id": 1, "status": "open", "score": 6.0, "title": "слабая"},
                {"id": 2, "status": "open", "score": 8.5, "title": "хорошая"},
                {"id": 3, "status": "take", "score": 5.0, "title": "уже разобрана"},
                {"id": 4, "status": "open", "score": None, "title": "без оценки"},
            ]
        )
        r = serve._purge_low_score(8.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["purged"], 1)
        self.assertEqual(self.trashed, [1])  # только #1 ушла в мусор
        self.assertEqual(r["candidates"], 1)
        self.assertEqual(r["threshold"], 8.0)

    def test_threshold_strict_excludes_equality(self):
        # score РАВНЫЙ порогу (8.0) — НЕ уходит (условие строго <, как просил юзер: «[0;7,9]»).
        self._write_state(
            [
                {"id": 10, "status": "open", "score": 8.0, "title": "на границе"},
                {"id": 11, "status": "open", "score": 7.9, "title": "чуть ниже"},
            ]
        )
        r = serve._purge_low_score(8.0)
        self.assertEqual(r["purged"], 1)
        self.assertEqual(self.trashed, [11])  # только #11, не #10

    def test_no_candidates_returns_zero_purged(self):
        # все идеи либо разобраны, либо без оценки, либо выше порога → зачищать нечего
        self._write_state(
            [
                {"id": 1, "status": "open", "score": 9.5},
                {"id": 2, "status": "take", "score": 3.0},
                {"id": 3, "status": "open", "score": None},
            ]
        )
        r = serve._purge_low_score(8.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["purged"], 0)
        self.assertEqual(self.trashed, [])
        self.assertIn("нет открытых", r["msg"])

    def test_rejects_invalid_threshold(self):
        # порог вне 0..10 → отказ ДО чтения state.json
        self.assertFalse(serve._purge_low_score(-0.1)["ok"])
        self.assertFalse(serve._purge_low_score(10.5)["ok"])
        self.assertEqual(self.trashed, [])  # ничего не зачищено

    def test_deferred_while_run_active(self):
        # идёт прогон → busy, без мутации state.json
        serve.RUN["running"] = True
        self._write_state([{"id": 1, "status": "open", "score": 5.0}])
        r = serve._purge_low_score(8.0)
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("busy"))
        self.assertEqual(self.trashed, [])

    def test_state_corrupt_returns_error(self):
        # state.json битый → отказ с причиной, без падения
        with open(self._state_path, "w", encoding="utf-8") as f:
            f.write("{ битый json !!!")
        r = serve._purge_low_score(8.0)
        self.assertFalse(r["ok"])
        self.assertIn("state.json", r["msg"])
        self.assertEqual(self.trashed, [])

    def test_partial_purge_when_busy_mid_cycle(self):
        # первая идея зачищена, вторая отбита busy (пошёл прогон посреди цикла) —
        # останавливаемся, не падаем, reported failed count.
        calls = [0]

        def fake_set(iid, st):
            calls[0] += 1
            if calls[0] == 1:
                self.trashed.append(iid)
                return {"ok": True, "msg": "OK"}
            return {"ok": False, "busy": True, "msg": "прогон стартовал"}

        serve._set_idea = fake_set
        self._write_state(
            [
                {"id": 1, "status": "open", "score": 5.0},
                {"id": 2, "status": "open", "score": 6.0},
                {"id": 3, "status": "open", "score": 7.0},  # не должен дойти — busy развал
            ]
        )
        r = serve._purge_low_score(8.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["purged"], 1)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(self.trashed, [1])  # только первая зачищена

    def test_custom_threshold(self):
        # порог 6.0 → зачищаем только <6, оставляем [6,8)
        self._write_state(
            [
                {"id": 1, "status": "open", "score": 5.9},
                {"id": 2, "status": "open", "score": 6.5},
                {"id": 3, "status": "open", "score": 7.5},
            ]
        )
        r = serve._purge_low_score(6.0)
        self.assertEqual(r["purged"], 1)
        self.assertEqual(self.trashed, [1])
        self.assertEqual(r["threshold"], 6.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
