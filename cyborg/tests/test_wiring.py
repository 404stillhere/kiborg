"""Тест обвязки _run_collect: env харвеста должен реально доходить до collect_source.

Регресс, найденный 2026-07-12 при добавлении источников: _run_collect игнорировал
переданный env и жёстко звал collect_source.run(inputs, {"n": 8, "source": "hn"}) —
harvest.py-шный SOURCE_N=30 и список sources реально не долетали до живого прогона.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wiring  # noqa: E402
import seen_items  # noqa: E402


class TestRunCollectPassesEnv(unittest.TestCase):
    def setUp(self):
        self._orig = wiring.collect_source.run

    def tearDown(self):
        wiring.collect_source.run = self._orig

    def test_n_and_sources_reach_collect_source(self):
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {"n": 30, "sources": ["hn", "reddit"]})
        self.assertEqual(captured["n"], 30)
        self.assertEqual(captured["sources"], ["hn", "reddit"])

    def test_default_env_still_uses_n8_hn(self):
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {})
        self.assertEqual(captured["n"], 8)
        self.assertEqual(captured["source"], "hn")
        self.assertNotIn("sources", captured)

    def test_telegram_creds_reach_collect_source(self):
        # регресс 2026-07-12 (2-й раз): telegram в sources есть, но креды НЕ прокидывались
        # через _run_collect -> источник тихо падал в partial_errors ("no channels configured").
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {
            "n": 30, "sources": ["hn", "telegram"],
            "telegram_channels": ["@a", "@b"], "telegram_api_id": "1",
            "telegram_api_hash": "h", "telegram_session": "s",
        })
        self.assertEqual(captured["telegram_channels"], ["@a", "@b"])
        self.assertEqual(captured["telegram_api_id"], "1")
        self.assertEqual(captured["telegram_api_hash"], "h")
        self.assertEqual(captured["telegram_session"], "s")

    def test_no_telegram_keys_when_absent(self):
        # без telegram-ключей в env их и не должно появляться в прокинутом словаре (не плодим None)
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {"n": 8})
        self.assertNotIn("telegram_channels", captured)
        self.assertNotIn("telegram_session", captured)


class TestRunCollectDoesNotFilter(unittest.TestCase):
    """Глаза (2026-07-13): фильтр «уже видели» переехал в Мозг (_run_ideate) — collect_source
    ТОЛЬКО смотрит и приносит всё, что увидел, даже если явно попросили filter_seen_items."""

    def setUp(self):
        self._orig_collect = wiring.collect_source.run

    def tearDown(self):
        wiring.collect_source.run = self._orig_collect

    def test_flag_has_no_effect_on_eyes(self):
        def fake_run(inputs, env):
            return {"items": [{"title": "A", "source": "hn", "id": 1},
                              {"title": "B", "source": "hn", "id": 2}], "degraded": False}
        wiring.collect_source.run = fake_run
        out = wiring._run_collect({}, {"filter_seen_items": True})
        self.assertEqual(len(out["items"]), 2)  # фильтра нет — приносит всё как увидел


class TestRunIdeateFilterSeenItems(unittest.TestCase):
    """Мозг (2026-07-13): filter_seen_items — только когда харвест явно попросил (иначе
    интерактивный 'приноси идеи' молча терял бы items, которые уже разбирал автономный харвест).
    Это теперь работа _run_ideate, не _run_collect — Глаза сами не помнят."""

    def setUp(self):
        self._orig_ideate = wiring.ideate.run
        self._orig_path = seen_items.PATH
        self._tmp = tempfile.mkdtemp(prefix="wiring_seen_")
        seen_items.PATH = os.path.join(self._tmp, "seen_items.json")

        def fake_run(inputs, env):
            return {"ideas": [], "n_in": len(inputs.get("items") or [])}
        wiring.ideate.run = fake_run

    def tearDown(self):
        wiring.ideate.run = self._orig_ideate
        seen_items.PATH = self._orig_path

    def test_flag_off_by_default_all_items_pass(self):
        items = [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}]
        out = wiring._run_ideate({"items": items}, {})
        self.assertEqual(out["n_in"], 2)
        out2 = wiring._run_ideate({"items": items}, {})  # без флага повторный вызов НЕ фильтрует
        self.assertEqual(out2["n_in"], 2)

    def test_flag_on_filters_across_calls(self):
        items = [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}]
        out1 = wiring._run_ideate({"items": items}, {"filter_seen_items": True})
        self.assertEqual(out1["n_in"], 2)   # первый раз — оба новые
        out2 = wiring._run_ideate({"items": items}, {"filter_seen_items": True})
        self.assertEqual(out2["n_in"], 0)   # второй раз — те же items, уже видели


class TestRunIdeateRankForcing(unittest.TestCase):
    """_run_ideate/_run_rank: форсируют k=6/keep=3 и резолвят модель через _content_llm
    (content_llm приоритетнее общего llm, не-callable не долетает до органа)."""

    def setUp(self):
        self._orig_ideate = wiring.ideate.run
        self._orig_rank = wiring.rank_ideas.run

    def tearDown(self):
        wiring.ideate.run = self._orig_ideate
        wiring.rank_ideas.run = self._orig_rank

    def test_ideate_forces_k6_no_llm_by_default(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        wiring.ideate.run = fake
        wiring._run_ideate({}, {})
        self.assertEqual(captured["k"], 6)
        self.assertNotIn("llm", captured)

    def test_ideate_prefers_content_llm_over_generic_llm(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        wiring.ideate.run = fake
        content, generic = (lambda p: "c"), (lambda p: "g")
        wiring._run_ideate({}, {"content_llm": content, "llm": generic})
        self.assertIs(captured["llm"], content)

    def test_ideate_falls_back_to_generic_llm(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        wiring.ideate.run = fake
        generic = lambda p: "g"
        wiring._run_ideate({}, {"llm": generic})
        self.assertIs(captured["llm"], generic)

    def test_ideate_ignores_non_callable_llm(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        wiring.ideate.run = fake
        wiring._run_ideate({}, {"llm": "не функция"})
        self.assertNotIn("llm", captured)

    def test_rank_forces_keep3_no_llm_by_default(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas_best": []}

        wiring.rank_ideas.run = fake
        wiring._run_rank({}, {})
        self.assertEqual(captured["keep"], 3)
        self.assertNotIn("llm", captured)

    def test_rank_prefers_content_llm(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas_best": []}

        wiring.rank_ideas.run = fake
        content = lambda p: "c"
        wiring._run_rank({}, {"content_llm": content, "llm": lambda p: "g"})
        self.assertIs(captured["llm"], content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
