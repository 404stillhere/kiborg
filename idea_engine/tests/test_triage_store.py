"""Тесты разбора идей (idea_engine/triage_store.py): taken/later — запись полных идей,
идемпотентность по id, атомарность, чтение. Сравните со test_rejected.py — здесь нет дедупа
по заголовку и нет потолка (взятые/отложенные не должны теряться)."""

import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import triage_store  # noqa: E402


class TestTriageStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="triage_")
        self._saved = (triage_store.TAKEN_PATH, triage_store.LATER_PATH, triage_store.DATA)
        self.taken = os.path.join(self.tmp, "taken.json")
        self.later = os.path.join(self.tmp, "later.json")
        triage_store.TAKEN_PATH = self.taken
        triage_store.LATER_PATH = self.later
        triage_store.DATA = self.tmp

    def tearDown(self):
        triage_store.TAKEN_PATH, triage_store.LATER_PATH, triage_store.DATA = self._saved

    def _idea(self, iid, title="Идея", why="почему"):
        return {"id": iid, "title": title, "why": why, "score": 8.0, "born_tick": 1, "status": "take"}

    def test_empty_when_no_file(self):
        self.assertEqual(triage_store.load(self.taken), {"taken": []})
        self.assertEqual(triage_store.load(self.later), {"later": []})
        self.assertEqual(triage_store.count(self.taken), 0)

    def test_add_stores_full_idea_with_triaged_ts(self):
        idea = self._idea(1, "Пульт киборга", "локальный агент")
        triage_store.add(self.taken, idea)
        items = triage_store.load(self.taken)["taken"]
        self.assertEqual(len(items), 1)
        # полная идея сохранена (все поля)
        self.assertEqual(items[0]["title"], "Пульт киборга")
        self.assertEqual(items[0]["score"], 8.0)
        self.assertEqual(items[0]["born_tick"], 1)
        # метка времени действия проставлена
        self.assertIn("triaged_ts", items[0])
        self.assertTrue(items[0]["triaged_ts"])  # не пустая строка

    def test_add_does_not_mutate_caller_dict(self):
        idea = self._idea(1)
        triage_store.add(self.later, idea)
        self.assertNotIn("triaged_ts", idea)  # caller's dict не тронут
        self.assertEqual(triage_store.count(self.later), 1)

    def test_idempotent_by_id(self):
        """Повторный triage той же идеи (тот же id) не дублирует — защита от гонки процессов."""
        triage_store.add(self.taken, self._idea(1))
        triage_store.add(self.taken, self._idea(1))  # тот же id
        self.assertEqual(triage_store.count(self.taken), 1)

    def test_different_ids_accumulate(self):
        triage_store.add(self.taken, self._idea(1, "А"))
        triage_store.add(self.taken, self._idea(2, "Б"))
        self.assertEqual(triage_store.count(self.taken), 2)

    def test_taken_and_later_are_separate_files(self):
        triage_store.add(self.taken, self._idea(1, "взял"))
        triage_store.add(self.later, self._idea(2, "позже"))
        self.assertEqual(triage_store.count(self.taken), 1)
        self.assertEqual(triage_store.count(self.later), 1)
        self.assertEqual(triage_store.load(self.taken)["taken"][0]["title"], "взял")
        self.assertEqual(triage_store.load(self.later)["later"][0]["title"], "позже")

    def test_no_cap_keeps_all(self):
        """В отличие от rejected (cap=200), taken/later не урезаются — взятые не теряются."""
        for i in range(300):
            triage_store.add(self.taken, self._idea(i))
        self.assertEqual(triage_store.count(self.taken), 300)

    def test_persists_atomic(self):
        triage_store.add(self.taken, self._idea(1))
        with open(self.taken, encoding="utf-8") as f:
            d = json.load(f)  # валидный JSON на диске
        self.assertEqual(d, {"taken": [json.loads(json.dumps(v)) for v in triage_store.load(self.taken)["taken"]]})
        self.assertFalse(any(p.endswith(".tmp") for p in os.listdir(self.tmp)))  # tmp убран

    def test_broken_file_falls_to_empty(self):
        with open(self.taken, "w", encoding="utf-8") as f:
            f.write("{битый")
        self.assertEqual(triage_store.load(self.taken), {"taken": []})  # не падаем
        self.assertEqual(triage_store.count(self.taken), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
