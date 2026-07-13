"""Тест органа finish_step (режим Б: «самый маленький шаг, чтобы доделать существующее»).

Орган читает карту проектов (recon.json), отбрасывает мёртвые / без next_step / из skip_folders,
достаёт next_step выбранного проекта с ротацией по cursor. `_load` мокается — без файла и сети.
Раньше орган был вообще НЕ покрыт (в idea_engine есть store/collect/ideate/rank/readability, finish_step — нет).
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import finish_step  # noqa: E402

CARDS = [
    {"folder": "proj-a", "state": "active", "next_step": "дописать README"},
    {"folder": "proj-b", "state": "active", "next_step": "починить тест"},
    {"folder": "proj-dead", "state": "dead", "next_step": "неважно"},       # dead -> отфильтр
    {"folder": "proj-nostep", "state": "active", "next_step": ""},          # без шага -> отфильтр
]


class TestFinishStep(unittest.TestCase):
    def setUp(self):
        self._orig_load = finish_step._load

    def tearDown(self):
        finish_step._load = self._orig_load

    def _with_cards(self, cards):
        finish_step._load = lambda path: cards

    def test_no_recon_path_returns_error_no_crash(self):
        out = finish_step.run({}, {})
        self.assertIsNone(out["nudge"])
        self.assertIn("no recon_path", out["error"])

    def test_load_failure_returns_error_no_crash(self):
        def boom(path):
            raise OSError("recon.json missing")
        finish_step._load = boom
        out = finish_step.run({}, {"recon_path": "x.json"})
        self.assertIsNone(out["nudge"])
        self.assertIn("missing", out["error"])

    def test_happy_path_picks_candidate_with_next_step(self):
        self._with_cards(CARDS)
        out = finish_step.run({}, {"recon_path": "x.json", "cursor": 0})
        self.assertIsNotNone(out["nudge"])
        self.assertEqual(out["nudge"]["folder"], "proj-a")
        self.assertEqual(out["nudge"]["kind"], "finish")
        self.assertIn("дописать README", out["nudge"]["why"])
        self.assertEqual(out["pool"], 2)              # только proj-a, proj-b (dead+nostep отфильтрованы)
        self.assertEqual(out["next_cursor"], 1)

    def test_cursor_rotates_and_wraps(self):
        self._with_cards(CARDS)
        f0 = finish_step.run({}, {"recon_path": "x.json", "cursor": 0})["nudge"]["folder"]
        f1 = finish_step.run({}, {"recon_path": "x.json", "cursor": 1})["nudge"]["folder"]
        f2 = finish_step.run({}, {"recon_path": "x.json", "cursor": 2})["nudge"]["folder"]
        self.assertEqual(f0, "proj-a")
        self.assertEqual(f1, "proj-b")                # cursor 1 -> следующий кандидат
        self.assertEqual(f2, "proj-a")               # cursor 2 % pool(2) -> оборот на первый

    def test_skip_folders_excludes_project(self):
        self._with_cards(CARDS)
        out = finish_step.run({}, {"recon_path": "x.json", "skip_folders": ["proj-a"]})
        self.assertEqual(out["pool"], 1)              # proj-a выкинут списком skip
        self.assertEqual(out["nudge"]["folder"], "proj-b")

    def test_dead_and_stepless_never_chosen(self):
        self._with_cards(CARDS)
        out = finish_step.run({}, {"recon_path": "x.json"})
        self.assertNotEqual(out["nudge"]["folder"], "proj-dead")
        self.assertNotEqual(out["nudge"]["folder"], "proj-nostep")

    def test_all_filtered_gives_empty_pool_no_crash(self):
        self._with_cards([
            {"folder": "d1", "state": "dead", "next_step": "x"},
            {"folder": "d2", "state": "abandoned", "next_step": "y"},
            {"folder": "d3", "state": "active", "next_step": ""},
        ])
        out = finish_step.run({}, {"recon_path": "x.json"})
        self.assertIsNone(out["nudge"])
        self.assertEqual(out["pool"], 0)

    def test_why_truncated_to_220(self):
        self._with_cards([{"folder": "big", "state": "active", "next_step": "x" * 500}])
        out = finish_step.run({}, {"recon_path": "x.json"})
        self.assertEqual(len(out["nudge"]["why"]), 220)

    def test_non_dict_card_skipped(self):
        self._with_cards(["не словарь", {"folder": "ok", "state": "active", "next_step": "go"}])
        out = finish_step.run({}, {"recon_path": "x.json"})
        self.assertEqual(out["pool"], 1)
        self.assertEqual(out["nudge"]["folder"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
