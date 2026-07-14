"""Тесты советников (cyborg/advisors.py) — тонкие места без сети/ключей.

Пока: арбитр (RankIdeasAdvisor) прокидывает руль направления из контекста в rank_ideas."""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import advisors  # noqa: E402

_OPTS = [{"id": "A", "title": "трекер сна", "why": "x"},
         {"id": "B", "title": "агрегатор рецептов", "why": "y"}]


class TestRankAdvisorDirection(unittest.TestCase):
    def test_direction_reaches_rank_env(self):
        captured = {}

        def fake_rank(inputs, env):
            captured.update(env)
            return {"ideas_best": [dict(inputs["ideas"][0], judged="llm")]}

        adv = advisors.RankIdeasAdvisor(rank_run=fake_rank)
        adv.opine("q", _OPTS, {"content_llm": lambda p: "m", "direction": "игры"})
        self.assertEqual(captured.get("direction"), "игры")   # арбитр судит с рулём

    def test_no_direction_no_key(self):
        captured = {}

        def fake_rank(inputs, env):
            captured.update(env)
            return {"ideas_best": [dict(inputs["ideas"][0], judged="llm")]}

        adv = advisors.RankIdeasAdvisor(rank_run=fake_rank)
        adv.opine("q", _OPTS, {"content_llm": lambda p: "m"})
        self.assertNotIn("direction", captured)               # без руля ключа нет


if __name__ == "__main__":
    unittest.main(verbosity=2)
