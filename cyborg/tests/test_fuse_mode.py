"""Тест фасада fuse_mode: состояние combo_hash (кольцо/атомарность) и отчёты CLI.

Живой прогон (сеть+LLM+доставка) здесь НЕ тестируется — это смоук через CLI
(python cyborg/fuse_mode.py --dry-run / живой). Тут только детерминированные части.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fuse_mode  # noqa: E402


class TestFusionState(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "fusion_state.json")
        self._orig = fuse_mode._state_path
        fuse_mode._state_path = lambda: self._path

    def tearDown(self):
        fuse_mode._state_path = self._orig
        self._tmp.cleanup()

    def test_load_missing_file_gives_default(self):
        st = fuse_mode._load_state()
        self.assertEqual(st, {"combos": {}, "last_seed": 0})

    def test_save_then_load_roundtrip(self):
        fuse_mode._save_state({"combos": {"abc": 123}, "last_seed": 7})
        st = fuse_mode._load_state()
        self.assertEqual(st["combos"], {"abc": 123})
        self.assertEqual(st["last_seed"], 7)
        self.assertFalse(os.path.exists(self._path + ".tmp"))  # атомарная запись не оставляет мусора

    def test_ring_caps_at_max_combos(self):
        combos = {"h%03d" % i: i for i in range(fuse_mode.MAX_COMBOS + 5)}  # 505 записей
        fuse_mode._save_state({"combos": combos, "last_seed": 1})
        st = fuse_mode._load_state()
        self.assertLessEqual(len(st["combos"]), fuse_mode.MAX_COMBOS)  # кольцо держит потолок
        self.assertNotIn("h000", st["combos"])  # самые старые вылетели
        self.assertIn("h504", st["combos"])  # свежие остались

    def test_broken_file_gives_default(self):
        with open(self._path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(fuse_mode._load_state(), {"combos": {}, "last_seed": 0})


class TestFormatReport(unittest.TestCase):
    def test_dry_run_shows_items_without_llm_noise(self):
        rep = {
            "ok": True,
            "dry_run": True,
            "seed": 11,
            "combo_hash": "abc",
            "picked": [{"source": "reddit", "title": "пост"}],
            "sources_missing": ["hn"],
            "reused_ids": [],
            "prompt": "ПРОМПТ",
        }
        text = "\n".join(fuse_mode.format_report(rep))
        self.assertIn("DRY-RUN", text)
        self.assertIn("seed=11", text)
        self.assertIn("[reddit] пост", text)
        self.assertIn("hn", text)
        self.assertIn("ПРОМПТ", text)

    def test_failure_shows_reason_and_violations(self):
        rep = {"ok": False, "reason": "нужно >=2 источников", "seed": 3, "violations": ["роли повторяются"]}
        text = "\n".join(fuse_mode.format_report(rep))
        self.assertIn("не удалась", text)
        self.assertIn("нужно >=2 источников", text)
        self.assertIn("роли повторяются", text)

    def test_success_shows_card_and_fusion_table(self):
        rep = {
            "ok": True,
            "seed": 5,
            "attempts": 2,
            "combo_repeated": False,
            "card": {
                "title": "Идея",
                "score": 7.5,
                "weak": False,
                "fusion": [{"source": "reddit", "role": "механизм", "took": "приём X", "collapse": "всё"}],
            },
        }
        text = "\n".join(fuse_mode.format_report(rep))
        self.assertIn("доставлена", text)
        self.assertIn("Идея", text)
        self.assertIn("7.5", text)
        self.assertIn("[reddit/механизм]", text)


if __name__ == "__main__":
    unittest.main()
