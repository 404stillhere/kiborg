"""Тест обвязки _run_collect: env харвеста должен реально доходить до collect_source.

Регресс, найденный 2026-07-12 при добавлении источников: _run_collect игнорировал
переданный env и жёстко звал collect_source.run(inputs, {"n": 8, "source": "hn"}) —
harvest.py-шный SOURCE_N=30 и список sources реально не долетали до живого прогона.
"""
import json
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
    """_run_ideate/_run_rank: форсируют k=12/keep=5 и резолвят модель через _content_llm
    (content_llm приоритетнее общего llm, не-callable не долетает до органа)."""

    def setUp(self):
        self._orig_ideate = wiring.ideate.run
        self._orig_rank = wiring.rank_ideas.run

    def tearDown(self):
        wiring.ideate.run = self._orig_ideate
        wiring.rank_ideas.run = self._orig_rank

    def test_ideate_forces_k12_no_llm_by_default(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        wiring.ideate.run = fake
        wiring._run_ideate({}, {})
        self.assertEqual(captured["k"], 12)
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

    def test_rank_forces_keep5_no_llm_by_default(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas_best": []}

        wiring.rank_ideas.run = fake
        wiring._run_rank({}, {})
        self.assertEqual(captured["keep"], 5)
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


class TestRunRankCouncil(unittest.TestCase):
    """_run_rank со включённым советом (гейт снят 2026-07-13). Проверяем: совет судит идеи,
    НЕТ двойного платного вызова судьи (регресс от скептика), откат по сбою/деградации/гварду."""

    # пул > keep(=5), иначе _rank_by_council вернёт «отбирать не из чего» до совета
    IDEAS = [{"title": "A", "why": "a"}, {"title": "B", "why": "b"}, {"title": "C", "why": "c"},
             {"title": "D", "why": "d"}, {"title": "E", "why": "e"}, {"title": "F", "why": "f"},
             {"title": "G", "why": "g"}]

    def setUp(self):
        self._orig_deliberate = wiring.mind.deliberate
        self._orig_rank = wiring.rank_ideas.run
        self.rank_calls = []

        def fake_rank(inputs, env):
            self.rank_calls.append(env)
            return {"ideas_best": [{"title": "FALLBACK"}]}

        wiring.rank_ideas.run = fake_rank

    def tearDown(self):
        wiring.mind.deliberate = self._orig_deliberate
        wiring.rank_ideas.run = self._orig_rank

    def test_council_two_voices_ranks_by_score_no_double_call(self):
        def fake_think(q, options, council, context):
            return {"live": ["rank_ideas", "ask_llm"], "degraded": False, "council_woken": False,
                    "scores": {0: 0.8, 1: 0.2, 2: 0.5, 3: 0.9, 4: 0.1, 5: 0.7, 6: 0.3}, "why": "тест"}

        wiring.mind.deliberate = fake_think
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertIn("council", out)
        self.assertFalse(out["council"]["solo"])
        # топ-5 по баллу: D(.9) A(.8) F(.7) C(.5) G(.3)
        self.assertEqual([i["title"] for i in out["ideas_best"]], ["D", "A", "F", "C", "G"])
        self.assertTrue(all(i["judged"] == "council" for i in out["ideas_best"]))
        self.assertEqual(self.rank_calls, [])   # НИ ОДНОГО повторного вызова судьи

    def test_solo_arbiter_reuses_no_second_judge_call(self):
        # интуиция промолчала -> 1 голос (арбитр). Переиспользуем его результат, НЕ зовём судью снова.
        def fake_think(q, options, council, context):
            return {"live": ["rank_ideas"], "degraded": False, "council_woken": False,
                    "scores": {0: 0.5, 1: 0.9, 2: 0.1, 3: 0.7, 4: 0.2, 5: 0.6, 6: 0.4}, "why": "solo"}

        wiring.mind.deliberate = fake_think
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertTrue(out["council"]["solo"])
        # топ-5 по баллу: B(.9) D(.7) F(.6) A(.5) G(.4)
        self.assertEqual([i["title"] for i in out["ideas_best"]], ["B", "D", "F", "A", "G"])
        self.assertTrue(all(i["judged"] == "solo" for i in out["ideas_best"]))
        self.assertEqual(self.rank_calls, [])   # ключевой регресс: второго платного вызова НЕТ

    def test_degraded_all_abstain_falls_back_to_single_judge(self):
        wiring.mind.deliberate = lambda q, o, c, ctx: {"live": [], "degraded": True, "scores": {}}
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertEqual(out, {"ideas_best": [{"title": "FALLBACK"}]})
        self.assertEqual(len(self.rank_calls), 1)   # ровно один — прежний судья

    def test_council_exception_silent_fallback(self):
        def boom(q, o, c, ctx):
            raise RuntimeError("совет упал")

        wiring.mind.deliberate = boom
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertEqual(out, {"ideas_best": [{"title": "FALLBACK"}]})   # конвейер не встал
        self.assertEqual(len(self.rank_calls), 1)

    def test_no_chain_no_council(self):
        called = []
        wiring.mind.deliberate = lambda *a, **k: called.append(1) or {"live": ["x", "y"], "scores": {}}
        out = wiring._run_rank({"ideas": self.IDEAS}, {})       # нет llm_chain/orchestra
        self.assertEqual(out, {"ideas_best": [{"title": "FALLBACK"}]})
        self.assertEqual(called, [])                            # в совет даже не заходили

    def test_council_false_flag_disables(self):
        called = []
        wiring.mind.deliberate = lambda *a, **k: called.append(1) or {"live": ["x", "y"], "scores": {}}
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}], "council": False})
        self.assertEqual(out, {"ideas_best": [{"title": "FALLBACK"}]})
        self.assertEqual(called, [])

    def test_small_pool_returns_as_is_without_council(self):
        called = []
        wiring.mind.deliberate = lambda *a, **k: called.append(1) or {}
        three = self.IDEAS[:3]                                  # <= keep=5 — отбирать не из чего
        out = wiring._run_rank({"ideas": three}, {"llm_chain": [{"id": "x"}]})
        self.assertEqual(out["ideas_best"], three)
        self.assertEqual(called, [])


class TestIntuitionNoCap(unittest.TestCase):
    """_IntuitionNoCap — интуиция БЕЗ потолка: payload к organ.js не должен нести max_tokens
    (иначе рассуждающие модели тратят лимит на обдумывание и молчат)."""

    def test_ask_payload_has_no_max_tokens(self):
        captured = {}

        class _Proc:
            returncode = 0
            stdout = json.dumps({"ok": True, "text": '{"scores":{"0":50}}'})

        def fake_run(cmd, input=None, **kw):
            captured["payload"] = json.loads(input)
            return _Proc()

        orig_run, orig_exists = wiring.subprocess.run, wiring.os.path.exists
        wiring.subprocess.run = fake_run
        wiring.os.path.exists = lambda p: True
        try:
            txt = wiring._IntuitionNoCap()._ask([{"id": "deepseek"}], "prompt", 45000)
        finally:
            wiring.subprocess.run = orig_run
            wiring.os.path.exists = orig_exists
        self.assertNotIn("max_tokens", captured["payload"]["inputs"])   # потолок снят
        self.assertIn("chain", captured["payload"]["env"])
        self.assertEqual(txt, '{"scores":{"0":50}}')


if __name__ == "__main__":
    unittest.main(verbosity=2)
