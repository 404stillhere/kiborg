"""Тест судьи идей rank_ideas: отбор топ-k по ответу судьи, фолбэк без судьи/на мусоре, границы."""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import rank_ideas  # noqa: E402

POOL = [{"title": f"Идея {i}", "why": "почему " + str(i)} for i in range(6)]


class TestRankIdeas(unittest.TestCase):
    def test_llm_picks_top(self):
        out = rank_ideas.run({"ideas": POOL}, {"keep": 3, "llm": lambda p: '{"top":[4,1,2]}'})
        best = out["ideas_best"]
        self.assertEqual([b["title"] for b in best], ["Идея 4", "Идея 1", "Идея 2"])
        self.assertTrue(all(b["judged"] == "llm" for b in best))

    def test_fallback_without_llm(self):
        out = rank_ideas.run({"ideas": POOL}, {"keep": 3})
        self.assertEqual([b["title"] for b in out["ideas_best"]], ["Идея 0", "Идея 1", "Идея 2"])
        self.assertTrue(all(b["judged"] == "fallback" for b in out["ideas_best"]))

    def test_fallback_on_garbage(self):
        out = rank_ideas.run({"ideas": POOL}, {"keep": 3, "llm": lambda p: "извините, не могу"})
        self.assertEqual(len(out["ideas_best"]), 3)
        self.assertTrue(all(b["judged"] == "fallback" for b in out["ideas_best"]))

    def test_pool_smaller_than_keep(self):
        out = rank_ideas.run({"ideas": POOL[:2]}, {"keep": 3, "llm": lambda p: '{"top":[0]}'})
        self.assertEqual(len(out["ideas_best"]), 2)     # отбирать не из чего — отдаём все

    def test_out_of_range_indices_fallback(self):
        out = rank_ideas.run({"ideas": POOL}, {"keep": 3, "llm": lambda p: '{"top":[99,100]}'})
        self.assertEqual(len(out["ideas_best"]), 3)     # мусорные индексы -> фолбэк первые 3

    def test_partial_top_fills_to_keep(self):
        # скептик #3: судья дал ОДИН индекс при keep=3 -> добираем до 3, идеи НЕ теряем
        out = rank_ideas.run({"ideas": POOL}, {"keep": 3, "llm": lambda p: '{"top":[4]}'})
        self.assertEqual(len(out["ideas_best"]), 3)
        self.assertEqual(out["ideas_best"][0]["title"], "Идея 4")   # выбор судьи первым, judged=llm
        self.assertEqual(out["ideas_best"][0]["judged"], "llm")
        self.assertTrue(all(b["judged"] == "fill" for b in out["ideas_best"][1:]))

    def test_parses_fenced_response(self):
        resp = 'Вот мой выбор:\n```json\n{"top":[5,0,3]}\n```'
        out = rank_ideas.run({"ideas": POOL}, {"keep": 3, "llm": lambda p: resp})
        self.assertEqual([b["title"] for b in out["ideas_best"]], ["Идея 5", "Идея 0", "Идея 3"])

    def test_dedup_indices_then_fill_to_keep(self):
        out = rank_ideas.run({"ideas": POOL}, {"keep": 3, "llm": lambda p: '{"top":[1,1,2]}'})
        titles = [b["title"] for b in out["ideas_best"]]
        self.assertEqual(titles, ["Идея 1", "Идея 2", "Идея 0"])  # дубль схлопнут + добор до keep
        self.assertEqual([b["judged"] for b in out["ideas_best"]], ["llm", "llm", "fill"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
