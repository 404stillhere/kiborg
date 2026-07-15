"""Тест каталога: реестр _shared/organs.json грузится и парсится в карточки."""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import json  # noqa: E402
import tempfile  # noqa: E402

from registry import load_catalog  # noqa: E402
from wiring import build_organs  # noqa: E402
import wiring  # noqa: E402


class TestRegistry(unittest.TestCase):
    def test_catalog_loads(self):
        cat = load_catalog()
        self.assertGreater(len(cat), 40)  # 89 карточек
        c = cat[0]
        self.assertTrue(c.name)
        self.assertIn(c.status, ("extracted", "draft", "candidate", None))

    def test_executable_organs_wire(self):
        orgs = build_organs()
        self.assertGreaterEqual(len(orgs), 6)  # collect/ideate/rank/finish/scrub/deliver/finish_sink
        names = [o.name for o in orgs]
        self.assertIn("collect_source", names)
        self.assertIn("ideate", names)
        self.assertIn("rank_ideas", names)
        self.assertIn("scrub_secrets", names)
        self.assertIn("deliver", names)
        # у каждого исполняемого органа — вызываемый run и роль
        for o in orgs:
            self.assertTrue(callable(o.run))
            self.assertIn(o.role, ("source", "transform", "sink"))

    def test_ideas_pipeline_chain(self):
        import brain
        import router
        orgs = build_organs()
        # терминал цели «идеи» доходит до delivered ЧЕРЕЗ судью и scrub
        self.assertEqual(brain.infer_deliverable("приноси свежие идеи", orgs), "delivered")
        # роутер отбирает ВЕСЬ конвейер идей (6 звеньев с редактором читаемости; k>=6 после
        # добавления readability_gate — раньше цепь была 5-звенной и k=5 хватало) и не тянет «доделай»
        picked = [o.name for o in router.route("приноси свежие идеи", orgs, k=6)]
        for n in ("collect_source", "ideate", "rank_ideas", "readability_gate", "scrub_secrets", "deliver"):
            self.assertIn(n, picked)
        self.assertNotIn("finish_sink", picked)


class TestFinishCursor(unittest.TestCase):
    """Курсор finish_step персистится и ротирует между прогонами (чинит мёртвый плумбинг)."""

    def test_cursor_persists_and_advances(self):
        orig_file, orig_fs = wiring._CURSOR_FILE, wiring.finish_step
        tmp = tempfile.mkdtemp()
        wiring._CURSOR_FILE = os.path.join(tmp, "c.json")

        class FakeFS:
            @staticmethod
            def run(inputs, env):
                c = env.get("cursor", 0)
                return {"nudge": {"folder": "proj%d" % c}, "next_cursor": c + 1}

        wiring.finish_step = FakeFS
        try:
            got = [wiring._run_finish({}, {})["nudge"]["folder"] for _ in range(3)]
            self.assertEqual(got, ["proj0", "proj1", "proj2"])  # ротация, не залипание
            with open(wiring._CURSOR_FILE, encoding="utf-8") as f:   # with — не течёт хэндл
                self.assertEqual(json.load(f)["cursor"], 3)
        finally:
            wiring._CURSOR_FILE, wiring.finish_step = orig_file, orig_fs


if __name__ == "__main__":
    unittest.main(verbosity=2)
