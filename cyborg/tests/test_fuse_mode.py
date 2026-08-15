"""Тест фасада fuse_mode: состояние combo_hash (кольцо/атомарность) и отчёты CLI.

Живой прогон (сеть+LLM+доставка) здесь НЕ тестируется — это смоук через CLI
(python cyborg/fuse_mode.py --dry-run / живой). Тут только детерминированные части.
"""

import json
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


class TestPoolGate(unittest.TestCase):
    """Авто-гейт ультры: пул материалов не менялся с прошлой доставки → LLM не зовётся."""

    ITEMS = [
        {"source": "hn", "id": "1", "title": "a"},
        {"source": "reddit", "id": "2", "title": "b"},
        {"source": "tg", "id": "3", "title": "c"},
    ]

    def test_signature_is_deterministic_and_order_independent(self):
        a = fuse_mode._pool_signature(self.ITEMS)
        b = fuse_mode._pool_signature(list(reversed(self.ITEMS)))  # порядок сбора — не состав пула
        self.assertEqual(a, b)
        self.assertEqual(a, fuse_mode._pool_signature(self.ITEMS))

    def test_signature_changes_on_new_material(self):
        changed = self.ITEMS + [{"source": "lobsters", "id": "9", "title": "x"}]
        self.assertNotEqual(fuse_mode._pool_signature(self.ITEMS), fuse_mode._pool_signature(changed))

    def test_gate_fires_only_on_same_pool_with_history(self):
        sig = fuse_mode._pool_signature(self.ITEMS)
        self.assertFalse(fuse_mode._gate_skip({"combos": {}}, sig))  # истории нет → не запирать
        self.assertFalse(fuse_mode._gate_skip({"pool_sig": "другой"}, sig))  # пул менялся → пускать
        self.assertTrue(fuse_mode._gate_skip({"pool_sig": sig}, sig))  # тот же пул → пропуск


class TestRunMetrics(unittest.TestCase):
    """fusion_runs.jsonl: по строке на каждый завершённый прогон (кроме dry-run)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_metrics = fuse_mode._metrics_path
        fuse_mode._metrics_path = lambda: os.path.join(self._tmp.name, "fusion_runs.jsonl")

    def tearDown(self):
        fuse_mode._metrics_path = self._orig_metrics
        self._tmp.cleanup()

    def _read(self):
        if not os.path.exists(fuse_mode._metrics_path()):
            return []
        with open(fuse_mode._metrics_path(), encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_success_line_carries_calibration_fields(self):
        # прогон-успех через обёртку: метрики пишутся даже когда карточка без score
        rec = {
            "ok": True,
            "card": {"combo_hash": "abc", "score": 8.9, "weak": True},
            "seed": 7,
            "combo_hash": "abc",
            "attempts": 1,
        }
        impl = fuse_mode._run_fusion_impl
        fuse_mode._run_fusion_impl = lambda **k: rec
        try:
            rep = fuse_mode.run_fusion(seed=7)
        finally:
            fuse_mode._run_fusion_impl = impl
        self.assertTrue(rep["ok"])
        lines = self._read()
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["attempts"], 1)  # метрика «успех с 1-й попытки»
        self.assertEqual(line["score"], 8.9)
        self.assertTrue(line["weak"])
        self.assertFalse(line["auto"])
        self.assertIn("ts", line)

    def test_gate_skip_is_logged_and_counted_separately(self):
        rec = {"ok": True, "skipped": "pool_unchanged", "reason": "пул не менялся", "seed": 5}
        impl = fuse_mode._run_fusion_impl
        fuse_mode._run_fusion_impl = lambda **k: rec
        try:
            fuse_mode.run_fusion(auto=True, dry_run=False)
        finally:
            fuse_mode._run_fusion_impl = impl
        line = self._read()[0]
        self.assertEqual(line["skipped"], "pool_unchanged")  # скипы отличимы от успехов
        self.assertTrue(line["auto"])

    def test_dry_run_writes_nothing(self):
        impl = fuse_mode._run_fusion_impl
        fuse_mode._run_fusion_impl = lambda **k: {"ok": True, "dry_run": True}
        try:
            fuse_mode.run_fusion(dry_run=True)
        finally:
            fuse_mode._run_fusion_impl = impl
        self.assertEqual(self._read(), [])  # dry-run — не прогон, в статистику не идёт


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

    def test_gate_skip_reports_quietly(self):
        rep = {"ok": True, "skipped": "pool_unchanged", "reason": "пул материалов не менялся с прошлой ультры", "seed": 9}
        text = "\n".join(fuse_mode.format_report(rep))
        self.assertIn("АВТО-ГАЙТ", text)
        self.assertIn("пул материалов не менялся", text)

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
