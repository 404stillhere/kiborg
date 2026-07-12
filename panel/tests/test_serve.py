"""Тесты сервера пульта (panel/serve.py) — чистые хелперы.

Прицел — не UI, а места, где сервер трогает диск и чужой ввод:
  1. _save_layout — ЕДИНСТВЕННАЯ запись POST-данных браузера на диск: валидатор + атомарность + потолок.
  2. _read_stash / _read_runs — парсинг файлов состояния, устойчивость к мусору/отсутствию.
  3. _set_idea — гейт статуса ДО subprocess (никаких сторонних значений в CLI).
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


class TestSaveLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_lay_")
        self.f = os.path.join(self.tmp, "layout.json")
        self._orig = serve.LAYOUT_FILE
        serve.LAYOUT_FILE = self.f

    def tearDown(self):
        serve.LAYOUT_FILE = self._orig

    def _read(self):
        with open(self.f, encoding="utf-8") as fh:
            return json.load(fh)

    def test_valid_roundtrip(self):
        serve._save_layout({"ideate": {"x": 10, "y": 20.44}})
        self.assertEqual(self._read(), {"ideate": {"x": 10.0, "y": 20.4}})  # округление до 0.1

    def test_rejects_bad_entries(self):
        serve._save_layout({
            "ok": {"x": 1, "y": 2},
            "": {"x": 1, "y": 2},                # пустой ключ
            "k" * 41: {"x": 1, "y": 2},          # ключ длиннее 40
            "not_dict": "строка",                # значение не словарь
            "no_y": {"x": 1},                    # нет y
            "str_xy": {"x": "1", "y": "2"},      # x/y не числа
            "bool_xy": {"x": True, "y": False},  # bool — не координата
        })
        self.assertEqual(list(self._read().keys()), ["ok"])

    def test_atomic_no_temp_and_overwrite(self):
        serve._save_layout({"a": {"x": 1, "y": 1}})
        serve._save_layout({"b": {"x": 2, "y": 2}})   # перезапись поверх существующего
        self.assertFalse(os.path.exists(self.f + ".tmp"))  # хвоста .tmp нет
        self.assertEqual(list(self._read().keys()), ["b"])  # файл цел, второе сохранение на диске

    def test_caps_key_count(self):
        big = {f"k{i}": {"x": i, "y": i} for i in range(serve._LAYOUT_MAX_KEYS + 30)}
        serve._save_layout(big)
        self.assertLessEqual(len(self._read()), serve._LAYOUT_MAX_KEYS)


class TestReadStash(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serve_st_")
        os.makedirs(os.path.join(self.tmp, "data"))
        self._orig = serve.CYBORG
        serve.CYBORG = self.tmp

    def tearDown(self):
        serve.CYBORG = self._orig

    def test_parses_and_skips_junk(self):
        p = os.path.join(self.tmp, "data", "idea_stash.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"title": "Первая", "brain": "llm"}) + "\n")
            f.write("\n")               # пустая строка — пропустить
            f.write("{битый json\n")    # мусор — пропустить, не падать
            f.write(json.dumps({"title": "Вторая"}) + "\n")
        r = serve._read_stash()
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["latest"][0]["title"], "Вторая")  # свежие сверху

    def test_missing_file_safe(self):
        self.assertEqual(serve._read_stash(), {"total": 0, "latest": []})


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
            f.write("- [2026-07-11 11:52:34] «приноси свежие идеи» → "
                    "collect_source -> ideate -> deliver | delivered=3\n")
        runs = serve._read_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["goal"], "приноси свежие идеи")
        self.assertEqual(runs[0]["chain"], ["collect_source", "ideate", "deliver"])
        self.assertEqual(runs[0]["deliverable"], "delivered")
        self.assertEqual(runs[0]["value"], "3")

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
        payload = {"checked_at": "2026-07-12 20:01:58", "degraded": False,
                   "sources": {"hn": {"items": 6, "ok": True, "error": None},
                               "reddit": {"items": 0, "ok": False, "error": "reddit: 403"}}}
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
