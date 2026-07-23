"""Тесты B1 council_weights — адаптивные веса советников (Feedback Cortex).

Хранит {enabled, weights:{ask_llm, orchestra, rank_ideas}, updated_after} в
data/council_weights.json. По умолчанию enabled=false (канон mind.WEIGHTS неизменен),
веса = исходные mind.WEIGHTS. load/save/is_enabled/current_weights — stdlib-only.
Scoped rebind в wiring_council (B2) читает current_weights() только когда is_enabled().
"""

import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import council_weights  # noqa: E402


class TestCouncilWeights(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cw_")
        self._orig = council_weights.PATH
        council_weights.PATH = os.path.join(self.tmp, "council_weights.json")

    def tearDown(self):
        council_weights.PATH = self._orig

    def test_default_disabled_when_no_file(self):
        # нет файла → enabled=false (канон), веса = mind.WEIGHTS (исходные)
        self.assertFalse(council_weights.is_enabled())
        w = council_weights.current_weights()
        self.assertEqual(w["rank_ideas"], 0.41)
        self.assertEqual(w["ask_llm"], 0.39)
        self.assertEqual(w["orchestra"], 0.20)

    def test_save_and_read(self):
        council_weights.save(
            {
                "enabled": True,
                "weights": {"ask_llm": 0.5, "orchestra": 0.1, "rank_ideas": 0.4},
            }
        )
        self.assertTrue(council_weights.is_enabled())
        w = council_weights.current_weights()
        self.assertEqual(w["ask_llm"], 0.5)

    def test_save_persists_atomic(self):
        council_weights.save({"enabled": False, "weights": council_weights.DEFAULT_WEIGHTS})
        with open(council_weights.PATH, encoding="utf-8") as f:
            json.load(f)
        self.assertFalse(os.path.exists(council_weights.PATH + ".tmp"))

    def test_broken_file_falls_back_to_default(self):
        with open(council_weights.PATH, "w", encoding="utf-8") as f:
            f.write("{ не json")
        self.assertFalse(council_weights.is_enabled())  # disabled = безопасный дефолт
        self.assertEqual(council_weights.current_weights(), council_weights.DEFAULT_WEIGHTS)

    def test_missing_weights_key_uses_default(self):
        with open(council_weights.PATH, "w", encoding="utf-8") as f:
            json.dump({"enabled": True}, f)  # нет weights → дефолт
        self.assertTrue(council_weights.is_enabled())
        self.assertEqual(council_weights.current_weights(), council_weights.DEFAULT_WEIGHTS)

    def test_partial_weights_merged_with_default(self):
        # если в weights только часть советников → остальные берутся из DEFAULT (не 0, не absent)
        council_weights.save({"enabled": True, "weights": {"ask_llm": 0.6}})
        w = council_weights.current_weights()
        self.assertEqual(w["ask_llm"], 0.6)
        self.assertEqual(w["rank_ideas"], 0.41)  # из DEFAULT
        self.assertEqual(w["orchestra"], 0.20)  # из DEFAULT

    def test_unknown_advisor_in_weights_dropped(self):
        # неизвестные имена выкидываются (только 3 канонических советника)
        council_weights.save(
            {
                "enabled": True,
                "weights": {"ask_llm": 0.5, "ghost": 0.9, "rank_ideas": 0.4},
            }
        )
        w = council_weights.current_weights()
        self.assertNotIn("ghost", w)
        self.assertEqual(set(w.keys()), set(council_weights.ALL_ADVISORS))


if __name__ == "__main__":
    unittest.main()
