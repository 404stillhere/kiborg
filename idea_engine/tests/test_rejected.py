"""Тесты списка отклонённых идей (idea_engine/rejected.py): запись, дедуп, recent, cap, атомарность."""
import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import rejected  # noqa: E402


class TestRejected(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rej_")
        self._saved = (rejected.PATH, rejected.DATA)
        rejected.DATA = self.tmp
        rejected.PATH = os.path.join(self.tmp, "rejected.json")

    def tearDown(self):
        rejected.PATH, rejected.DATA = self._saved

    def test_empty_when_no_file(self):
        self.assertEqual(rejected.recent(), [])
        self.assertEqual(rejected.count(), 0)
        self.assertEqual(rejected.load(), {"rejected": []})

    def test_add_and_recent(self):
        rejected.add("Плохая идея", "не надо")
        self.assertEqual(rejected.recent(), ["Плохая идея"])
        self.assertEqual(rejected.count(), 1)
        self.assertEqual(rejected.load()["rejected"][0]["why"], "не надо")

    def test_dedup_by_title_case_insensitive(self):
        rejected.add("Трекер сна")
        rejected.add("трекер СНА")            # тот же заголовок в другом регистре — не копим
        self.assertEqual(rejected.count(), 1)

    def test_empty_title_ignored(self):
        rejected.add("   ")
        rejected.add("")
        self.assertEqual(rejected.count(), 0)

    def test_recent_returns_last_n(self):
        for i in range(10):
            rejected.add(f"идея {i}")
        self.assertEqual(rejected.recent(3), ["идея 7", "идея 8", "идея 9"])

    def test_capped_at_max(self):
        for i in range(rejected._MAX + 20):
            rejected.add(f"идея {i}")
        self.assertEqual(rejected.count(), rejected._MAX)   # помним последние N, файл не пухнет

    def test_persists_atomic(self):
        rejected.add("одна")
        with open(rejected.PATH, encoding="utf-8") as f:
            json.load(f)                                     # валидный JSON на диске
        self.assertFalse(any(p.endswith(".tmp") for p in os.listdir(self.tmp)))

    def test_broken_file_falls_to_empty(self):
        with open(rejected.PATH, "w", encoding="utf-8") as f:
            f.write("{битый")
        self.assertEqual(rejected.recent(), [])             # не падаем


if __name__ == "__main__":
    unittest.main(verbosity=2)
