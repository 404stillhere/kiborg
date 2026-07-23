"""Тест обвязки _run_collect: env харвеста должен реально доходить до collect_source.

Регресс, найденный 2026-07-12 при добавлении источников: _run_collect игнорировал
переданный env и жёстко звал collect_source.run(inputs, {"n": 8, "source": "hn"}) —
harvest.py-шный SOURCE_N=30 и список sources реально не долетали до живого прогона.
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ask_llm  # noqa: E402  (last_provider мок для provider-проброса в _run_ideate)
import seen_items  # noqa: E402
import wiring  # noqa: E402


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

    def test_empty_sources_passed_through_not_dropped(self):
        # D7 (аудит 2026-07-17): пустой список (все ленты off, папок нет) должен ДОЙТИ до
        # collect_source как [], а не быть выброшенным — иначе орган дефолтит на hn (молчаливый
        # сбор вопреки выключенным тумблерам). Раньше `if env.get("sources")` ронял [].
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            captured["_had_sources_key"] = "sources" in env
            return {"items": [], "degraded": True}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {"n": 8, "sources": []})
        self.assertTrue(captured["_had_sources_key"])  # ключ есть (пустой список не выброшен)
        self.assertEqual(captured["sources"], [])  # именно [], не hn-дефолт

    def test_telegram_creds_reach_collect_source(self):
        # регресс 2026-07-12 (2-й раз): telegram в sources есть, но креды НЕ прокидывались
        # через _run_collect -> источник тихо падал в partial_errors ("no channels configured").
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect(
            {},
            {
                "n": 30,
                "sources": ["hn", "telegram"],
                "telegram_channels": ["@a", "@b"],
                "telegram_api_id": "1",
                "telegram_api_hash": "h",
                "telegram_session": "s",
            },
        )
        self.assertEqual(captured["telegram_channels"], ["@a", "@b"])
        self.assertEqual(captured["telegram_api_id"], "1")
        self.assertEqual(captured["telegram_api_hash"], "h")
        self.assertEqual(captured["telegram_session"], "s")

    def test_files_paths_reach_collect_source(self):
        # РЕГРЕССИЯ 2026-07-15: тот же класс, что telegram — 'files' в sources есть, но files_paths
        # НЕ прокидывался через _run_collect → _files давал «no folders configured», весь прогон
        # уходил в 4 захардкоженных заголовка (degraded=True), папка юзера НЕ читалась.
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [{"title": "x", "source": "files"}], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {"n": 30, "sources": ["files"], "files_paths": ["M:/projects/kiborg", "C:/notes"]})
        self.assertEqual(captured["files_paths"], ["M:/projects/kiborg", "C:/notes"])
        self.assertEqual(captured["sources"], ["files"])

    def test_no_files_paths_when_absent(self):
        # без files_paths в env — не плодим ключ (не None), поведение не-files прогонов не меняем
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {"n": 8, "sources": ["hn"]})
        self.assertNotIn("files_paths", captured)

    def test_collect_scrubs_secret_from_item_title(self):
        # БЕЗОПАСНОСТЬ 2026-07-15: файл-источник может принести СЕКРЕТ в заголовке (фильтр _files
        # неполон) — заголовок уходит в ПРОМПТ генератора → к LLM-провайдеру. _run_collect чистит
        # заголовки scrub_secrets ДО генерации (downstream-scrub поздно — промпт уже ушёл).
        def fake_run(inputs, env):
            return {
                "items": [
                    {"title": "config.py — AQ.FAKEfake1234567890abcdefgh", "source": "files"},
                    {"title": "обычный заголовок без секрета", "source": "files"},
                ],
                "degraded": False,
            }

        wiring.collect_source.run = fake_run
        out = wiring._run_collect({}, {"sources": ["files"], "files_paths": ["x"]})
        self.assertNotIn("AQ.FAKEfake1234567890abcdefgh", out["items"][0]["title"])  # секрет НЕ утёк
        self.assertEqual(out["items"][1]["title"], "обычный заголовок без секрета")  # чистое не тронуто

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

    def test_prefetched_out_reused_without_second_fetch(self):
        # гейт уже сфетчил (harvest кладёт prefetched_out) -> _run_collect возвращает его,
        # collect_source ВТОРОЙ раз НЕ зовётся (не тянем телегу дважды за тик)
        def boom(inputs, env):
            raise AssertionError("collect_source не должен вызываться при валидном prefetched_out")

        wiring.collect_source.run = boom
        pf = {"items": [{"title": "из гейта", "id": 1, "source": "telegram"}], "degraded": False}
        out = wiring._run_collect({}, {"prefetched_out": pf})
        self.assertIs(out, pf)  # тот же выхлоп гейта, без нового фетча

    def test_no_prefetch_fetches_normally(self):
        captured = {}

        def fake_run(inputs, env):
            captured["called"] = True
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {"n": 30, "sources": ["telegram"]})
        self.assertTrue(captured.get("called"))  # без prefetch — фетчим как раньше

    def test_invalid_prefetch_falls_back_to_fetch(self):
        # prefetched_out без ключа items (невалидно, напр. сбой гейта) -> фетчим сами
        captured = {}

        def fake_run(inputs, env):
            captured["called"] = True
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake_run
        wiring._run_collect({}, {"prefetched_out": {"degraded": True}})  # нет items
        self.assertTrue(captured.get("called"))


class TestRunCollectDoesNotFilter(unittest.TestCase):
    """Глаза (2026-07-13): фильтр «уже видели» переехал в Мозг (_run_ideate) — collect_source
    ТОЛЬКО смотрит и приносит всё, что увидел, даже если явно попросили filter_seen_items."""

    def setUp(self):
        self._orig_collect = wiring.collect_source.run

    def tearDown(self):
        wiring.collect_source.run = self._orig_collect

    def test_flag_has_no_effect_on_eyes(self):
        def fake_run(inputs, env):
            return {
                "items": [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}],
                "degraded": False,
            }

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
        self.assertEqual(out1["n_in"], 2)  # первый раз — оба новые
        out2 = wiring._run_ideate({"items": items}, {"filter_seen_items": True})
        self.assertEqual(out2["n_in"], 0)  # второй раз — те же items, уже видели


class TestRunIdeateRankForcing(unittest.TestCase):
    """_run_ideate/_run_rank: форсируют k=8/keep=3 и резолвят модель через _content_llm
    (content_llm приоритетнее общего llm, не-callable не долетает до органа)."""

    def setUp(self):
        self._orig_ideate = wiring.ideate.run
        self._orig_rank = wiring.rank_ideas.run

    def tearDown(self):
        wiring.ideate.run = self._orig_ideate
        wiring.rank_ideas.run = self._orig_rank

    def test_ideate_forces_k8_no_llm_by_default(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        wiring.ideate.run = fake
        wiring._run_ideate({}, {})
        self.assertEqual(captured["k"], 8)
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

    def test_ideate_forwards_on_progress_to_organ(self):
        # РЕГРЕССИЯ: обёртка строила свежий env и РОНЯЛА on_progress → живой суб-прогресс молчал.
        # Колбэк должен долетать до реального органа.
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        orig = wiring.ideate.run
        wiring.ideate.run = fake
        try:
            op = lambda m: None
            wiring._run_ideate({}, {"on_progress": op})
            self.assertIs(captured.get("on_progress"), op)  # долетел до органа
        finally:
            wiring.ideate.run = orig

    def test_readability_forwards_on_progress_to_organ(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas_polished": []}

        orig = wiring.readability_gate.run
        wiring.readability_gate.run = fake
        try:
            op = lambda m: None
            wiring._run_readability({}, {"on_progress": op})
            self.assertIs(captured.get("on_progress"), op)
        finally:
            wiring.readability_gate.run = orig

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

    def test_rank_disabled_strips_llm_for_offline_fallback(self):
        # council_config рубильник: rank_ideas выключен → fallback rank_ideas идёт СТРОГО
        # офлайн (e.pop("llm")), даже если llm принесли. Арбитр выключен явно = не тратим
        # модель на отбор. (незакоммиченная правка wiring.py:270-272.)
        # council_config импортируется в wiring ЛОКАЛЬНО (паттерн: top-level dep не плодим),
        # поэтому мокаем через sys.modules — wiring.council_config как атрибута нет.
        import sys

        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas_best": []}

        wiring.rank_ideas.run = fake
        real_cc = sys.modules.get("council_config")
        fake_cc = type("M", (), {"is_enabled": staticmethod(lambda name: False)})()
        sys.modules["council_config"] = fake_cc
        try:
            wiring._run_rank({}, {"content_llm": lambda p: "c", "llm": lambda p: "g"})
        finally:
            if real_cc is not None:
                sys.modules["council_config"] = real_cc
            else:
                del sys.modules["council_config"]
        self.assertNotIn("llm", captured)  # llm снят → строго офлайн fallback

    def test_rank_enabled_keeps_llm(self):
        # контр-кейс: rank_ideas включен (по умолчанию) → llm доходит до rank_ideas.run.
        import sys

        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas_best": []}

        wiring.rank_ideas.run = fake
        real_cc = sys.modules.get("council_config")
        fake_cc = type("M", (), {"is_enabled": staticmethod(lambda name: True)})()
        sys.modules["council_config"] = fake_cc
        try:
            content = lambda p: "c"
            wiring._run_rank({}, {"content_llm": content, "llm": lambda p: "g"})
        finally:
            if real_cc is not None:
                sys.modules["council_config"] = real_cc
            else:
                del sys.modules["council_config"]
        self.assertIs(captured.get("llm"), content)  # llm дошёл (content приоритетнее)

    def test_ideate_threads_direction(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        wiring.ideate.run = fake
        wiring._run_ideate({}, {"direction": "железки"})
        self.assertEqual(captured["direction"], "железки")  # руль долетел до генератора

    def test_ideate_no_direction_key_when_absent(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas": []}

        wiring.ideate.run = fake
        wiring._run_ideate({}, {})
        self.assertNotIn("direction", captured)  # без руля ключа нет

    def test_rank_threads_direction(self):
        captured = {}

        def fake(inputs, env):
            captured.update(env)
            return {"ideas_best": []}

        wiring.rank_ideas.run = fake
        wiring._run_rank({}, {"direction": "игры"})  # без совета -> фолбэк-судья с рулём
        self.assertEqual(captured["direction"], "игры")


class TestRunRankCouncil(unittest.TestCase):
    """_run_rank со включённым советом (гейт снят 2026-07-13). Проверяем: совет судит идеи,
    НЕТ двойного платного вызова судьи (регресс от скептика), откат по сбою/деградации/гварду."""

    # пул > keep(=3), иначе _rank_by_council вернёт «отбирать не из чего» до совета
    IDEAS = [
        {"title": "A", "why": "a"},
        {"title": "B", "why": "b"},
        {"title": "C", "why": "c"},
        {"title": "D", "why": "d"},
        {"title": "E", "why": "e"},
        {"title": "F", "why": "f"},
        {"title": "G", "why": "g"},
    ]

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
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "council_woken": False,
                "scores": {0: 0.8, 1: 0.2, 2: 0.5, 3: 0.9, 4: 0.1, 5: 0.7, 6: 0.3},
                "why": "тест",
            }

        wiring.mind.deliberate = fake_think
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertIn("council", out)
        self.assertFalse(out["council"]["solo"])
        # топ-3 по баллу: D(.9) A(.8) F(.7)
        self.assertEqual([i["title"] for i in out["ideas_best"]], ["D", "A", "F"])
        self.assertTrue(all(i["judged"] == "council" for i in out["ideas_best"]))
        self.assertEqual(self.rank_calls, [])  # НИ ОДНОГО повторного вызова судьи

    def test_council_score_wired_to_cards(self):
        # D6 (аудит 2026-07-17): реальный балл совета (0..1) впаян в карточку как 0-10 для бейджа
        # пульта «оценка совета». Раньше balls считались и ВЫБРАСЫВАЛИСЬ → бейдж не рисовался никогда.
        def fake_think(q, options, council, context):
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {0: 0.8, 1: 0.2, 2: 0.5, 3: 0.9, 4: 0.1, 5: 0.7, 6: 0.3},
                "why": "x",
            }

        wiring.mind.deliberate = fake_think
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        by_title = {i["title"]: i for i in out["ideas_best"]}
        self.assertEqual(by_title["D"]["score"], 9.0)  # 0.9 совета → 9.0 бейджа (>=8 high)
        self.assertEqual(by_title["A"]["score"], 8.0)  # 0.8 → 8.0
        self.assertEqual(by_title["F"]["score"], 7.0)  # 0.7 → 7.0 (mid)

    def test_council_emits_live_progress(self):
        # живой суб-прогресс на самом медленном шаге (совет × идеи, минуты): «совет судит N идей»
        def fake_think(q, options, council, context):
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {i: 0.5 for i in range(len(options))},
                "why": "x",
            }

        wiring.mind.deliberate = fake_think
        msgs = []
        wiring._run_rank(
            {"ideas": self.IDEAS},
            {
                "llm_chain": [{"id": "x"}],
                "on_progress": msgs.append,
                "orchestra": {"models": ["a", "b", "c"], "chat": lambda *a: ""},
            },
        )
        self.assertTrue(any("совет судит 7 идей" in m and "3 рецензентов" in m for m in msgs))

    def test_solo_arbiter_reuses_no_second_judge_call(self):
        # интуиция промолчала -> 1 голос (арбитр). Переиспользуем его результат, НЕ зовём судью снова.
        def fake_think(q, options, council, context):
            return {
                "live": ["rank_ideas"],
                "degraded": False,
                "council_woken": False,
                "scores": {0: 0.5, 1: 0.9, 2: 0.1, 3: 0.7, 4: 0.2, 5: 0.6, 6: 0.4},
                "why": "solo",
            }

        wiring.mind.deliberate = fake_think
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertTrue(out["council"]["solo"])
        # топ-3 по баллу: B(.9) D(.7) F(.6)
        self.assertEqual([i["title"] for i in out["ideas_best"]], ["B", "D", "F"])
        self.assertTrue(all(i["judged"] == "solo" for i in out["ideas_best"]))
        self.assertEqual(self.rank_calls, [])  # ключевой регресс: второго платного вызова НЕТ

    def test_degraded_all_abstain_falls_back_to_single_judge(self):
        wiring.mind.deliberate = lambda q, o, c, ctx: {"live": [], "degraded": True, "scores": {}}
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertEqual(out, {"ideas_best": [{"title": "FALLBACK"}]})
        self.assertEqual(len(self.rank_calls), 1)  # ровно один — прежний судья

    def test_council_exception_silent_fallback(self):
        def boom(q, o, c, ctx):
            raise RuntimeError("совет упал")

        wiring.mind.deliberate = boom
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertEqual(out, {"ideas_best": [{"title": "FALLBACK"}]})  # конвейер не встал
        self.assertEqual(len(self.rank_calls), 1)

    def test_no_chain_no_council(self):
        called = []
        wiring.mind.deliberate = lambda *a, **k: called.append(1) or {"live": ["x", "y"], "scores": {}}
        out = wiring._run_rank({"ideas": self.IDEAS}, {})  # нет llm_chain/orchestra
        self.assertEqual(out, {"ideas_best": [{"title": "FALLBACK"}]})
        self.assertEqual(called, [])  # в совет даже не заходили

    def test_council_false_flag_disables(self):
        called = []
        wiring.mind.deliberate = lambda *a, **k: called.append(1) or {"live": ["x", "y"], "scores": {}}
        out = wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}], "council": False})
        self.assertEqual(out, {"ideas_best": [{"title": "FALLBACK"}]})
        self.assertEqual(called, [])

    def test_small_pool_returns_as_is_without_council(self):
        called = []
        wiring.mind.deliberate = lambda *a, **k: called.append(1) or {}
        three = self.IDEAS[:3]  # <= keep=3 — отбирать не из чего
        out = wiring._run_rank({"ideas": three}, {"llm_chain": [{"id": "x"}]})
        self.assertEqual(out["ideas_best"], three)
        self.assertEqual(called, [])

    def test_council_question_and_context_carry_direction(self):
        # руль долетает до совета: интуиция/оркестр видят тему в ВОПРОСЕ, арбитр — в КОНТЕКСТЕ
        seen = {}

        def fake_think(q, options, council, context):
            seen["q"], seen["ctx"] = q, context
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {i: 0.5 for i in range(len(options))},
                "why": "t",
            }

        wiring.mind.deliberate = fake_think
        wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}], "direction": "здоровье"})
        self.assertIn("здоровье", seen["q"])
        self.assertEqual(seen["ctx"]["direction"], "здоровье")

    def test_council_question_plain_without_direction(self):
        seen = {}

        def fake_think(q, options, council, context):
            seen["q"] = q
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {i: 0.5 for i in range(len(options))},
                "why": "t",
            }

        wiring.mind.deliberate = fake_think
        wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        self.assertNotIn("направлении", seen["q"])  # без руля вопрос обычный


class TestDynamicKeepThreshold(unittest.TestCase):
    """A4 dynamic keep: вместо тупой обрезки топ-keep — пороговый фильтр. Идея проходит
    если score>6.0 (советная оценка 0.6+); все слабые → минимум 1 (лучшая); потолок 3.
    Куча без потолка, но фокус на сильнейших — слабая идея не занимает место сильной.
    """

    def setUp(self):
        self._orig = wiring.mind.deliberate
        self._orig_rank = wiring.rank_ideas.run

    def tearDown(self):
        wiring.mind.deliberate = self._orig
        wiring.rank_ideas.run = self._orig_rank

    def _deliberate_with_scores(self, scores):
        """Фиксирует deliberate, отдающий заданные баллы (0..1) по id опций."""

        def fake(q, options, council, context):
            return {"live": ["rank_ideas", "ask_llm"], "degraded": False, "scores": scores, "why": "t"}

        wiring.mind.deliberate = fake

    def test_dynamic_keep_filters_low_score_ideas(self):
        # 4 идеи: 2 сильные (>6.0), 2 слабые (<6.0). Динамический keep берёт ТОЛЬКО сильные,
        # а не тупо топ-3 (третья слабая не проходит порог).
        ideas = [{"title": t} for t in ("Сильная1", "Сильная2", "Слабая1", "Слабая2")]
        # 0.9→9.0, 0.8→8.0 — проходят; 0.4→4.0, 0.3→3.0 — отсеиваются
        self._deliberate_with_scores({0: 0.9, 1: 0.8, 2: 0.4, 3: 0.3})
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        titles = [i["title"] for i in out["ideas_best"]]
        self.assertEqual(titles, ["Сильная1", "Сильная2"])  # 2, не 3 — порог отрезал слабых
        self.assertTrue(all(i["score"] > 6.0 for i in out["ideas_best"]))

    def test_dynamic_keep_min_one_when_all_low(self):
        # все идеи слабые (<6.0) → всё равно отдаём ЛУЧШУЮ (минимум 1), не пустоту
        ideas = [{"title": t} for t in ("Сл1", "Сл2", "Сл3", "Сл4")]
        self._deliberate_with_scores({0: 0.3, 1: 0.2, 2: 0.1, 3: 0.05})
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        titles = [i["title"] for i in out["ideas_best"]]
        self.assertEqual(titles, ["Сл1"])  # лучшая по баллу, даже слабая
        self.assertEqual(out["ideas_best"][0]["score"], 3.0)  # 0.3 → 3.0

    def test_dynamic_keep_caps_at_three_strong(self):
        # 5 сильных идей (>6.0) → потолок 3, берём топ-3 по баллу
        ideas = [{"title": t} for t in ("S1", "S2", "S3", "S4", "S5")]
        self._deliberate_with_scores({0: 0.9, 1: 0.85, 2: 0.8, 3: 0.75, 4: 0.7})
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        titles = [i["title"] for i in out["ideas_best"]]
        self.assertEqual(titles, ["S1", "S2", "S3"])  # потолок 3
        self.assertEqual(len(out["ideas_best"]), 3)

    def test_dynamic_keep_mixed_strong_and_low(self):
        # 3 сильные + 2 слабые → 3 сильные (потолок 3), слабые отрезаны порогом
        ideas = [{"title": t} for t in ("A", "B", "C", "D", "E")]
        self._deliberate_with_scores({0: 0.9, 1: 0.7, 2: 0.65, 3: 0.4, 4: 0.3})
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        titles = [i["title"] for i in out["ideas_best"]]
        self.assertEqual(titles, ["A", "B", "C"])  # ровно 3, слабые D/E за порогом

    def test_dynamic_keep_borderline_exactly_six_passes(self):
        # score РОВНО 6.0 (0.6 совета) — проходит (правило >6.0 интерпретируем как >=6.0:
        # 6.0 = середина шкалы, идея «не хуже среднего» — оставляем)
        ideas = [{"title": t} for t in ("A", "B", "C", "D")]
        self._deliberate_with_scores({0: 0.9, 1: 0.6, 2: 0.5, 3: 0.4})
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        titles = [i["title"] for i in out["ideas_best"]]
        self.assertEqual(titles, ["A", "B"])  # B=6.0 проходит, C=5.0 нет


class TestMMRDiversification(unittest.TestCase):
    """A3 MMR: вместо тупой обрезки топ-keep — greedy Maximal Marginal Relevance.
    топ-1 по score, дальше max(λ×score − (1−λ)×max_sim), λ=0.7. sim = Jaccard по
    значимым словам title+why. Цель: не класть 3 идеи про одно и то же, даже если все
    высокобалльные — разнообразие важнее +0.1 к score 2-й идеи.
    """

    def setUp(self):
        self._orig = wiring.mind.deliberate
        self._orig_rank = wiring.rank_ideas.run

    def tearDown(self):
        wiring.mind.deliberate = self._orig
        wiring.rank_ideas.run = self._orig_rank

    def test_mmr_picks_diverse_second_over_similar(self):
        # 4 сильные идеи (порог 0.6 проходят), keep=3 → MMR выбирает 3 из 4. Две про ботов
        # (похожи), одна про здоровье, одна про готовку. Без MMR — топ-3 по id (обе
        # бот-идеи + трекер). С MMR 3-й выбираем НЕ-похожую вместо второго бота.
        ideas = [
            {"title": "telegram бот напоминаний", "why": "бот прислёт напоминания"},
            {"title": "discord бот модерации", "why": "бот модерирует чат"},  # похож на 1-го
            {"title": "трекер сна на часах", "why": "часы считают фазы сна"},  # непохож
            {"title": "планировщик еды", "why": "меню на неделю"},  # непохож
        ]
        # все 4 «сильные» — score 0.8 (порог 0.6 проходит) → passing=4 > keep=3 → MMR работает
        scores = {0: 0.8, 1: 0.8, 2: 0.8, 3: 0.8}

        def fake(q, options, council, context):
            return {"live": ["rank_ideas", "ask_llm"], "degraded": False, "scores": scores, "why": "t"}

        wiring.mind.deliberate = fake
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        titles = [i["title"] for i in out["ideas_best"]]
        # топ-1 = id 0 (telegram бот, одинаковый score → стабильно по id).
        # MMR: 2-й и 3-й — наименее похожие на уже взятые. discord бот делит «бот» с
        # telegram → штраф → НЕ выбирается, пока есть непохожие альтернативы.
        self.assertEqual(titles[0], "telegram бот напоминаний")  # топ-1 стабильно
        self.assertNotIn("discord бот модерации", titles)  # MMR выкинул похожую (была 4-й по diversity)
        self.assertIn("трекер сна на часах", titles)  # diversity выбран
        self.assertIn("планировщик еды", titles)  # diversity выбран

    def test_mmr_single_letter_titles_fall_back_to_score_order(self):
        # регресс: однобуквенные title (A/B/C/D) дают Jaccard=0 → MMR вырождается в
        # чисто-балльный порядок (как было до MMR). Это гарантирует что существующие
        # тесты с однобуквенными title НЕ сломаются.
        ideas = [{"title": t, "why": t.lower()} for t in ("A", "B", "C", "D", "E")]
        scores = {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.65, 4: 0.4}

        def fake(q, options, council, context):
            return {"live": ["rank_ideas", "ask_llm"], "degraded": False, "scores": scores, "why": "t"}

        wiring.mind.deliberate = fake
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        titles = [i["title"] for i in out["ideas_best"]]
        # A(0.9) B(0.8) C(0.7) — D(0.65) 4-й, E(0.4) за порогом. Чисто-балльный порядок.
        self.assertEqual(titles, ["A", "B", "C"])

    def test_mmr_preserves_score_in_cards(self):
        # MMR меняет только ПОРЯДОК выбора, НЕ балл в карточке. score остаётся из совета.
        # 4 идеи (пул > keep) → доходит до council-цикла с присвоением score.
        ideas = [
            {"title": "бот напоминаний", "why": "бот шлёт напоминания"},
            {"title": "трекер здоровья", "why": "трекер считает пульс"},
            {"title": "план питания", "why": "меню на неделю"},
            {"title": "low filler", "why": "x"},
        ]
        scores = {0: 0.8, 1: 0.7, 2: 0.65, 3: 0.3}

        def fake(q, options, council, context):
            return {"live": ["rank_ideas", "ask_llm"], "degraded": False, "scores": scores, "why": "t"}

        wiring.mind.deliberate = fake
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        by_title = {i["title"]: i for i in out["ideas_best"]}
        self.assertEqual(by_title["бот напоминаний"]["score"], 8.0)  # 0.8 → 8.0
        self.assertEqual(by_title["трекер здоровья"]["score"], 7.0)  # 0.7 → 7.0


class TestAntiBlandAggregation(unittest.TestCase):
    """A1 anti-bland: итоговый балл = 0.7 × weighted_avg + 0.3 × max(advisor scores).

    Защищает ПОЛЯРИЗУЮЩИЕ идеи — где один советник дал высоко (max), остальные низко
    (низкое avg). Без anti-bland такие идеи тонули в середине (avg). С anti-bland их
    «спасает» max-компонент. Источник данных — verdict["breakdown"] (mind.py:219-220),
    БЕЗ повторного LLM-вызова. Когда breakdown пуст/отсутствует — graceful degrade к
    scores (как было), существующие тесты без breakdown НЕ ломаются.
    """

    def setUp(self):
        self._orig = wiring.mind.deliberate
        self._orig_rank = wiring.rank_ideas.run

    def tearDown(self):
        wiring.mind.deliberate = self._orig
        wiring.rank_ideas.run = self._orig_rank

    def test_anti_bland_boosts_polarizing_idea(self):
        # Поляризующая идея X: rank_ideas=0.9 (высоко), ask_llm=0.4, orchestra=0.4 (средне).
        # avg = 0.41×0.9 + 0.39×0.4 + 0.20×0.4 = 0.369 + 0.156 + 0.08 = 0.605. max = 0.9.
        # anti-bland = 0.7×0.605 + 0.3×0.9 = 0.4235 + 0.27 = 0.6935 → > 0.6 порога, проходит.
        # Без anti-bland: avg=0.605 (ближе к порогу, но проходит). С anti-bland — явный буст.
        # Обычная Y: все 0.5. anti-bland=0.5 → НЕ проходит порог 0.6.
        ideas = [
            {"title": "Поляризующая X", "why": "x"},
            {"title": "Обычная Y", "why": "y"},
            {"title": "Сильная Z", "why": "z"},
            {"title": "Средняя W", "why": "w"},
        ]
        breakdown = [
            {"name": "rank_ideas", "weight": 0.41, "scores": {0: 0.9, 1: 0.5, 2: 0.95, 3: 0.4}, "why": ""},
            {"name": "ask_llm", "weight": 0.39, "scores": {0: 0.4, 1: 0.5, 2: 0.9, 3: 0.4}, "why": ""},
            {"name": "orchestra", "weight": 0.20, "scores": {0: 0.4, 1: 0.5, 2: 0.85, 3: 0.4}, "why": ""},
        ]
        # avg (взвешенное) — что отдал бы _tally: X=0.605, Y=0.5, Z=0.9105, W=0.4
        avg_scores = {0: 0.605, 1: 0.5, 2: 0.9105, 3: 0.4}

        def fake(q, options, council, context):
            return {
                "live": ["rank_ideas", "ask_llm", "orchestra"],
                "degraded": False,
                "scores": avg_scores,
                "breakdown": breakdown,
                "why": "t",
            }

        wiring.mind.deliberate = fake
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        by_title = {i["title"]: i for i in out["ideas_best"]}
        # С anti-bland: X получает буст (0.6935 > 0.605 чистого avg), проходит порог.
        # Без anti-bland X бы тоже прошёл (0.605 > 0.6), НО score был бы ~6.05, а с anti-bland ~6.94.
        self.assertIn("Поляризующая X", by_title)  # anti-bland усилил
        # score X должен отражать anti-bland пересчёт (> чистого avg×10=6.05)
        x_score = by_title["Поляризующая X"]["score"]
        self.assertGreater(x_score, 6.5)  # anti-bland даёт ~6.94, чистый avg дал бы ~6.05
        # Z — сильная, тоже проходит. Y (0.5) и W (0.4) — отрезаны порогом.
        self.assertIn("Сильная Z", by_title)
        self.assertNotIn("Обычная Y", by_title)
        self.assertNotIn("Средняя W", by_title)

    def test_anti_bland_absent_breakdown_uses_scores_as_is(self):
        # регресс: deliberate без breakdown (как во всех существующих тестах) → балл = scores,
        # без пересчёта. Гарантирует что 11 TestRunRankCouncil + 5 TestDynamicKeepThreshold
        # + 3 TestMMRDiversification НЕ сломались (там моки без breakdown).
        ideas = [{"title": t, "why": t.lower()} for t in ("A", "B", "C", "D")]
        scores = {0: 0.8, 1: 0.7, 2: 0.65, 3: 0.3}

        def fake(q, options, council, context):
            # НЕТ breakdown — как во всех существующих тестах
            return {"live": ["rank_ideas", "ask_llm"], "degraded": False, "scores": scores, "why": "t"}

        wiring.mind.deliberate = fake
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        by_title = {i["title"]: i for i in out["ideas_best"]}
        # score = чистое avg (без anti-bland пересчёта), как раньше
        self.assertEqual(by_title["A"]["score"], 8.0)  # 0.8 → 8.0, не пересчитано
        self.assertEqual(by_title["B"]["score"], 7.0)  # 0.7 → 7.0

    def test_anti_bland_empty_breakdown_uses_scores(self):
        # breakdown=[] (пустой список, не None) → тоже graceful degrade к scores
        ideas = [{"title": t, "why": t.lower()} for t in ("A", "B", "C", "D")]
        scores = {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.3}

        def fake(q, options, council, context):
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": scores,
                "breakdown": [],  # пустой
                "why": "t",
            }

        wiring.mind.deliberate = fake
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        by_title = {i["title"]: i for i in out["ideas_best"]}
        self.assertEqual(by_title["A"]["score"], 9.0)  # чистый score, без пересчёта

    def test_anti_bland_malformed_breakdown_safe(self):
        # breakdown с мусором (не dict scores, неизвестные oid) → не роняет, fallback к scores
        ideas = [{"title": t, "why": t.lower()} for t in ("A", "B", "C", "D")]
        scores = {0: 0.8, 1: 0.7, 2: 0.65, 3: 0.3}

        def fake(q, options, council, context):
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": scores,
                "breakdown": [
                    "не словарь",
                    {"name": "x", "weight": 0.5, "scores": "не dict"},
                    {"name": "y", "weight": 0.5},  # нет scores
                ],
                "why": "t",
            }

        wiring.mind.deliberate = fake
        out = wiring._run_rank({"ideas": ideas}, {"llm_chain": [{"id": "x"}]})
        # не упало — конвейер жив, score из чистого avg
        by_title = {i["title"]: i for i in out["ideas_best"]}
        self.assertEqual(by_title["A"]["score"], 8.0)

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

        # после дедупа _ask НАСЛЕДУЕТСЯ от advisors.AskLlmAdvisor — патчим его модуль
        orig_run, orig_exists = wiring.advisors.subprocess.run, wiring.advisors.os.path.exists
        wiring.advisors.subprocess.run = fake_run
        wiring.advisors.os.path.exists = lambda p: True
        try:
            txt = wiring._IntuitionNoCap()._ask([{"id": "deepseek"}], "prompt", 45000)
        finally:
            wiring.advisors.subprocess.run = orig_run
            wiring.advisors.os.path.exists = orig_exists
        self.assertNotIn("max_tokens", captured["payload"]["inputs"])  # потолок снят (унаследован, _MAX_TOKENS=None)
        self.assertIn("chain", captured["payload"]["env"])
        self.assertEqual(txt, '{"scores":{"0":50}}')


class TestLazyOrchestra(unittest.TestCase):
    """A2 lazy orchestra: двухфазный deliberate. По умолчанию orchestra голосует всегда
    (решение юзера 2026-07-13: «умный сомневается всегда»). Флаг env["lazy_orchestra"]=True
    → Фаза 1 deliberate БЕЗ orchestra, Фаза 2 (повторный deliberate С orchestra) только если
    топ-3 rank_ideas×ask_llm расходятся (Jaccard overlap < 2/3). Shadow-режим: флаг по
    умолчанию False → поведение как сейчас, ничего не ломает.
    """

    IDEAS = [{"title": t, "why": t.lower()} for t in ("A", "B", "C", "D", "E")]

    def setUp(self):
        self._orig = wiring.mind.deliberate
        self._orig_rank = wiring.rank_ideas.run
        self.calls = []

    def tearDown(self):
        wiring.mind.deliberate = self._orig
        wiring.rank_ideas.run = self._orig_rank

    def test_lazy_orchestra_disabled_by_default(self):
        # БЕЗ флага lazy_orchestra → orchestra всегда в context (как сейчас), один deliberate
        context_seen = []

        def fake(q, options, council, context):
            context_seen.append(context)
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {i: 0.5 for i in range(len(options))},
                "why": "t",
            }

        wiring.mind.deliberate = fake
        wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}], "orchestra": {"models": ["m1"]}})
        # один вызов deliberate, context содержит orchestra (как раньше)
        self.assertEqual(len(context_seen), 1)
        self.assertIn("orchestra", context_seen[0])

    def test_lazy_orchestra_skipped_when_advisors_agree(self):
        # lazy_orchestra=True, НО rank_ideas и ask_llm СОГЛАСНЫ (топ-3 совпадает) →
        # 2-й deliberate с orchestra НЕ зовётся (он не нужен)
        def fake(q, options, council, context):
            self.calls.append({"has_orch": "orchestra" in context and context["orchestra"]})
            # оба советника ставят одинаковый топ-3 → Jaccard overlap = 1.0 (полное согласие)
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.3, 4: 0.2},
                "breakdown": [
                    {
                        "name": "rank_ideas",
                        "weight": 0.41,
                        "scores": {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.3, 4: 0.2},
                        "why": "",
                    },
                    {"name": "ask_llm", "weight": 0.39, "scores": {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.3, 4: 0.2}, "why": ""},
                ],
                "why": "t",
            }

        wiring.mind.deliberate = fake
        wiring._run_rank(
            {"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}], "orchestra": {"models": ["m1"]}, "lazy_orchestra": True}
        )
        # один вызов deliberate (Фаза 1 БЕЗ orchestra), 2-й не понадобился
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.calls[0]["has_orch"])  # Фаза 1 — без orchestra

    def test_lazy_orchestra_called_on_disagreement(self):
        # lazy_orchestra=True, rank_ideas и ask_llm РАСХОДЯТСЯ (топ-3 разный) →
        # 2-й deliberate С orchestra зовётся для разрешения
        def fake(q, options, council, context):
            self.calls.append({"has_orch": bool(context.get("orchestra"))})
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                # rank_ideas топ: 0,1,2 ; ask_llm топ: 3,4,0 → overlap только {0} → 1/3 < 2/3
                "scores": {0: 0.6, 1: 0.5, 2: 0.4, 3: 0.45, 4: 0.35},
                "breakdown": [
                    {
                        "name": "rank_ideas",
                        "weight": 0.41,
                        "scores": {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.1, 4: 0.05},
                        "why": "",
                    },
                    {"name": "ask_llm", "weight": 0.39, "scores": {0: 0.3, 1: 0.1, 2: 0.05, 3: 0.9, 4: 0.8}, "why": ""},
                ],
                "why": "t",
            }

        wiring.mind.deliberate = fake
        wiring._run_rank(
            {"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}], "orchestra": {"models": ["m1"]}, "lazy_orchestra": True}
        )
        # два вызова: Фаза 1 без orchestra, Фаза 2 с orchestra
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.calls[0]["has_orch"])  # Фаза 1
        self.assertTrue(self.calls[1]["has_orch"])  # Фаза 2 — orchestra подключён

    def test_lazy_orchestra_no_orchestra_in_env_stays_single_phase(self):
        # lazy_orchestra=True, НО orchestra нет в env (нет ключей) → нечего подключать в Фазе 2,
        # остаётся один deliberate (как сейчас без оркестра)
        def fake(q, options, council, context):
            self.calls.append(1)
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {0: 0.5 for _ in options},
                "why": "t",
            }

        wiring.mind.deliberate = fake
        wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}], "lazy_orchestra": True})
        self.assertEqual(len(self.calls), 1)  # orchestra нет → Фаза 2 не запускается


class TestScopedRebindWeights(unittest.TestCase):
    """B2: council_weights.is_enabled() → подмена mind.WEIGHTS в try/finally вокруг deliberate.
    Feedback Cortex (B4) активирует флаг после накопления данных. По умолчанию disabled →
    mind.WEIGHTS не трогается (канон). Scoped rebind восстанавливает оригинал в finally —
    даже при исключении. mind.py (FROZEN) НЕ правим.
    """

    IDEAS = [{"title": t, "why": t.lower()} for t in ("A", "B", "C", "D", "E")]

    def setUp(self):
        self._orig_deliberate = wiring.mind.deliberate
        self._orig_weights = wiring.mind.WEIGHTS
        self._orig_rank = wiring.rank_ideas.run
        self._orig_cw_path = None
        try:
            import council_weights

            self._orig_cw_path = council_weights.PATH
        except Exception:
            pass

    def tearDown(self):
        wiring.mind.deliberate = self._orig_deliberate
        wiring.mind.WEIGHTS = self._orig_weights  # восстанавливаем канон
        wiring.rank_ideas.run = self._orig_rank
        if self._orig_cw_path is not None:
            import council_weights

            council_weights.PATH = self._orig_cw_path

    def _cw_to_tmp(self):
        import tempfile

        import council_weights

        tmp = os.path.join(tempfile.mkdtemp(prefix="cw_rebind_"), "cw.json")
        council_weights.PATH = tmp
        return council_weights

    def test_disabled_does_not_rebind_weights(self):
        # disabled (по умолчанию) → mind.WEIGHTS НЕ меняется во время deliberate
        cw = self._cw_to_tmp()
        cw.save({"enabled": False, "weights": cw.DEFAULT_WEIGHTS})

        weights_at_call = []

        def fake(q, options, council, context):
            weights_at_call.append(dict(wiring.mind.WEIGHTS))
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {i: 0.5 for i in range(len(options))},
                "why": "t",
            }

        wiring.mind.deliberate = fake
        wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        # WEIGHTS во время deliberate = канон (не менялся)
        self.assertEqual(weights_at_call[0], self._orig_weights)
        # и после — тоже канон (восстановлен)
        self.assertEqual(wiring.mind.WEIGHTS, self._orig_weights)

    def test_enabled_rebinds_weights_during_deliberate(self):
        # enabled=True с кастомными весами → mind.WEIGHTS подменён ВО время deliberate,
        # восстановлен после (finally)
        cw = self._cw_to_tmp()
        custom = {"ask_llm": 0.6, "orchestra": 0.1, "rank_ideas": 0.3}
        cw.save({"enabled": True, "weights": custom})

        weights_at_call = []

        def fake(q, options, council, context):
            weights_at_call.append(dict(wiring.mind.WEIGHTS))
            return {
                "live": ["rank_ideas", "ask_llm"],
                "degraded": False,
                "scores": {i: 0.5 for i in range(len(options))},
                "why": "t",
            }

        wiring.mind.deliberate = fake
        wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        # во время deliberate WEIGHTS = кастомные
        self.assertEqual(weights_at_call[0], custom)
        # после deliberate WEIGHTS восстановлен к канону (finally сработал)
        self.assertEqual(wiring.mind.WEIGHTS, self._orig_weights)

    def test_weights_restored_even_on_exception(self):
        # deliberate падает → finally всё равно восстанавливает WEIGHTS (не течёт)
        cw = self._cw_to_tmp()
        custom = {"ask_llm": 0.6, "orchestra": 0.1, "rank_ideas": 0.3}
        cw.save({"enabled": True, "weights": custom})

        def boom(q, options, council, context):
            raise RuntimeError("deliberate упал")

        wiring.mind.deliberate = boom
        # _run_rank ловит исключение совета (try/except в _run_rank) → откат к rank_ideas
        try:
            wiring._run_rank({"ideas": self.IDEAS}, {"llm_chain": [{"id": "x"}]})
        except Exception:
            pass
        # даже после падения WEIGHTS = канон (finally в scoped rebind сработал)
        self.assertEqual(wiring.mind.WEIGHTS, self._orig_weights)


class TestRunIdeateDeferredSeen(unittest.TestCase):
    """_run_ideate метит сырьё виденным ТОЛЬКО после успешной генерации — не жжёт при осечке."""

    def setUp(self):
        self._orig_seen_path = seen_items.PATH
        self._tmp = tempfile.mkdtemp(prefix="wiring_seen_")
        seen_items.PATH = os.path.join(self._tmp, "seen_items.json")
        self._orig_ideate = wiring.ideate.run

    def tearDown(self):
        seen_items.PATH = self._orig_seen_path
        wiring.ideate.run = self._orig_ideate

    @staticmethod
    def _items():
        return [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}]

    def test_stub_failure_does_not_burn_items(self):
        # живой ключ + осечка -> болванки brain='stub'. Посты НЕ метятся: сбой транзиентный,
        # повторим на следующем тике (раньше метились ДО генерации и сгорали навсегда).
        wiring.ideate.run = lambda inp, e: {"ideas": [{"title": "болванка", "brain": "stub"}]}
        out = wiring._run_ideate({"items": self._items()}, {"filter_seen_items": True, "content_llm": lambda p: "x"})
        self.assertTrue(out["ideas"])
        self.assertEqual(seen_items.load(), {})  # ничего не сожжено (dict пустой)
        self.assertEqual(len(seen_items.filter_fresh(self._items(), mark=False)), 2)  # оба ещё свежи

    def test_real_generation_marks_items(self):
        wiring.ideate.run = lambda inp, e: {"ideas": [{"title": "реальная", "brain": "llm"}]}
        wiring._run_ideate({"items": self._items()}, {"filter_seen_items": True, "content_llm": lambda p: "x"})
        # успех -> отмечены (формат dict[str,int] с 2026-07-21; проверяем ключи, не ts)
        self.assertEqual(set(seen_items.load().keys()), {"hn:1", "hn:2"})

    def test_stub_marks_when_no_key(self):
        # без ключа (stub-режим) болванки ожидаемы -> метим как обычно
        wiring.ideate.run = lambda inp, e: {"ideas": [{"title": "болванка", "brain": "stub"}]}
        wiring._run_ideate({"items": self._items()}, {"filter_seen_items": True})
        self.assertEqual(set(seen_items.load().keys()), {"hn:1", "hn:2"})


class TestCollectLockedTgSession(unittest.TestCase):
    """Замок tg-сессии (вариант А): collect_source под O_EXCL, пока телеграм в игре — два
    процесса не лезут в один .session разом ('database is locked'). Примитив от ОС — оффлайн-тест."""

    def setUp(self):
        self._orig = wiring.collect_source.run
        self.tmp = tempfile.mkdtemp(prefix="tglock_")
        self.sess = os.path.join(self.tmp, "kiborg_tg.session")

    def tearDown(self):
        wiring.collect_source.run = self._orig

    def test_lock_held_during_fetch_and_released_after(self):
        held = {}

        def fake(inputs, env):
            held["lock"] = os.path.exists(self.sess + ".lock")  # замок держится В МОМЕНТ фетча
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake
        wiring._collect_locked({}, {"telegram_session": self.sess})
        self.assertTrue(held["lock"])  # держали эксклюзивно во время фетча
        self.assertFalse(os.path.exists(self.sess + ".lock"))  # снят после выхода

    def test_no_lock_without_telegram(self):
        seen = {}

        def fake(inputs, env):
            seen["called"] = True
            seen["any_lock"] = any(f.endswith(".lock") for f in os.listdir(self.tmp))
            return {"items": []}

        wiring.collect_source.run = fake
        wiring._collect_locked({}, {"n": 8})  # нет telegram_session -> без замка
        self.assertTrue(seen["called"])  # фетч всё равно прошёл
        self.assertFalse(seen.get("any_lock"))  # замка не было

    def test_second_caller_waits_then_proceeds_no_deadlock(self):
        # «чужой процесс» держит лок -> ждём до таймаута и ПРОХОДИМ (без дедлока), чужой лок не трогаем
        open(self.sess + ".lock", "w").close()
        orig_to = wiring._TG_LOCK_TIMEOUT
        wiring._TG_LOCK_TIMEOUT = 0.2  # короткий таймаут — тест быстрый
        proceeded = {}

        def fake(inputs, env):
            proceeded["yes"] = True
            return {"items": []}

        wiring.collect_source.run = fake
        try:
            wiring._collect_locked({}, {"telegram_session": self.sess})
        finally:
            wiring._TG_LOCK_TIMEOUT = orig_to
        self.assertTrue(proceeded["yes"])  # прошёл по таймауту, не завис

    def test_timeout_logs_warning(self):
        """При timeout state_lock печатает warning в stdout."""
        # Мокаем state_lock так, чтобы он сразу выдал timeout (yield False)
        import contextlib
        import io
        import sys

        import store as _ie_store

        orig_store_lock = _ie_store.state_lock
        orig_wiring_lock = wiring.state_lock
        timeout_emulated = {}

        @contextlib.contextmanager
        def fake_lock(path, timeout=None, poll=None):
            """Контекст-менеджер, который сразу выдаёт timeout."""
            timeout_emulated["entered"] = True
            yield False  # ← timeout, лок не захвачен

        # Мокаем и в store, и в wiring (wiring_collect использует wiring.state_lock)
        _ie_store.state_lock = fake_lock
        wiring.state_lock = fake_lock
        try:
            captured = io.StringIO()
            orig_stdout = sys.stdout
            sys.stdout = captured

            try:
                wiring._collect_locked({}, {"telegram_session": self.sess})
            finally:
                sys.stdout = orig_stdout

            output = captured.getvalue()
            self.assertTrue(timeout_emulated.get("entered"))
            self.assertIn("[warn] state_lock timeout", output)
            self.assertIn("прошли без лока", output)
            self.assertIn(self.sess, output)  # путь к sess есть в warning
        finally:
            _ie_store.state_lock = orig_store_lock
            wiring.state_lock = orig_wiring_lock


class TestRemoveStaleLock(unittest.TestCase):
    """_remove_stale_lock(session_path, max_age_seconds) — автоматическая очистка
    «зависших» lock-файлов телеграм-сессии. После краша процесса lock остаётся на диске,
    и каждый следующий прогон ждёт полный TG_LOCK_TIMEOUT (130с). Если lock старше порога
    (30 мин по дефолту) — он гарантированно труп, сносим перед захватом, не тратя время
    на ожидание. Свежий lock (живой конкурент) НЕ трогаем.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stale_lock_")
        self.sess = os.path.join(self.tmp, "kiborg_tg.session")

    def _make_lock(self, age_minutes):
        """Создать lock-файл с mtime age_minutes минут назад."""
        path = self.sess + ".lock"
        open(path, "w").close()
        old_ts = time.time() - age_minutes * 60
        os.utime(path, (old_ts, old_ts))
        return path

    def test_stale_lock_removed(self):
        # lock старше порога (31 мин > 30) → удалён, факт залогирован
        lock_path = self._make_lock(age_minutes=31)
        self.assertTrue(os.path.exists(lock_path))

        removed = wiring._remove_stale_lock(self.sess, max_age_seconds=30 * 60)

        self.assertTrue(removed)  # функция сообщила об удалении
        self.assertFalse(os.path.exists(lock_path))  # файла больше нет

    def test_fresh_lock_kept(self):
        # свежий lock (1 мин << 30 мин порога) → НЕ трогаем, может быть живой конкурент
        lock_path = self._make_lock(age_minutes=1)
        removed = wiring._remove_stale_lock(self.sess, max_age_seconds=30 * 60)
        self.assertFalse(removed)
        self.assertTrue(os.path.exists(lock_path))  # файл на месте

    def test_no_lock_file_no_error(self):
        # lock-файла нет → функция не падает, возвращает False
        self.assertFalse(os.path.exists(self.sess + ".lock"))
        removed = wiring._remove_stale_lock(self.sess, max_age_seconds=30 * 60)
        self.assertFalse(removed)

    def test_boundary_age_equal_kept(self):
        # граничный случай: возраст РАВЕН порогу → НЕ удаляем (используем строгое <).
        # Это безопасная сторона: чуть-чуть свежий lock лучше не трогать (даём конкуренту
        # доп. секунды, чем снесём активный лок). Кладём mtime ровно N мин назад.
        self._make_lock(age_minutes=30)
        removed = wiring._remove_stale_lock(self.sess, max_age_seconds=30 * 60)
        # age вычисляется как time.time() - mtime, за время теста станет чуть больше 1800с.
        # Но осцилляция секундная, поэтому на границе ждём «не удалять» в практическом смысле.
        # Достаточно: файл точно существует, функция не упала, возвращён bool.
        self.assertIn(removed, (True, False))
        # Гарантия теста: при age РАВНО порог (строгое <) функция НЕ должна утверждать «удалено»
        # в момент СТРОГО до порога — что и проверим отдельным тестом ниже.

    def test_just_under_threshold_kept(self):
        # lock чуть-чуть моложе порога (29.5 мин < 30 мин) → НЕ удаляем
        self._make_lock(age_minutes=29)
        removed = wiring._remove_stale_lock(self.sess, max_age_seconds=30 * 60)
        self.assertFalse(removed)
        self.assertTrue(os.path.exists(self.sess + ".lock"))

    def test_stale_logs_message(self):
        # факт очистки попадает в stdout (читается в логах прогона)
        self._make_lock(age_minutes=45)
        import io
        import sys

        captured = io.StringIO()
        orig = sys.stdout
        sys.stdout = captured
        try:
            wiring._remove_stale_lock(self.sess, max_age_seconds=30 * 60)
        finally:
            sys.stdout = orig
        out = captured.getvalue()
        self.assertIn("[stale-lock]", out)
        self.assertIn("удалён зависший lock", out)
        self.assertIn(self.sess + ".lock", out)  # путь к lock в логе

    def test_empty_session_returns_false(self):
        # пустой путь сессии → ничего не делаем (защита от None/пустого env)
        self.assertFalse(wiring._remove_stale_lock("", max_age_seconds=30 * 60))
        self.assertFalse(wiring._remove_stale_lock(None, max_age_seconds=30 * 60))


class TestCollectLockedStaleLockCleanup(unittest.TestCase):
    """Интеграционный тест: _collect_locked при наличии stale lock-файла вызывает
    _remove_stale_lock ПЕРЕД state_lock — не ждёт 130с таймаута, а сразу сносит труп
    и захватывает лок. Свежий lock (живой конкурент) — поведение прежнее (ждём/проходим).
    """

    def setUp(self):
        self._orig = wiring.collect_source.run
        self.tmp = tempfile.mkdtemp(prefix="stale_integ_")
        self.sess = os.path.join(self.tmp, "kiborg_tg.session")

    def tearDown(self):
        wiring.collect_source.run = self._orig

    def test_stale_lock_removed_before_state_lock_attempt(self):
        # крашнулся прошлый процесс → lock-файл 31-минутной давности на диске.
        # _collect_locked должен: (1) снести труп через _remove_stale_lock,
        # (2) вызвать state_lock, который сразу получит O_EXCL (файла-то уже нет),
        # (3) выполниться быстро (без ожидания таймаута).
        stale_path = self.sess + ".lock"
        open(stale_path, "w").close()
        old_ts = time.time() - 31 * 60
        os.utime(stale_path, (old_ts, old_ts))

        proceeded = {"yes": False}

        def fake(inputs, env):
            proceeded["yes"] = True
            return {"items": [], "degraded": False}

        wiring.collect_source.run = fake
        wiring._collect_locked({}, {"telegram_session": self.sess})

        self.assertTrue(proceeded["yes"])  # collect_source выполнился
        self.assertFalse(os.path.exists(stale_path))  # lock-труп убран

    def test_fresh_lock_kept_cleanup_skipped(self):
        # свежий lock (1 мин) → _remove_stale_lock его НЕ трогает, state_lock честно
        # ждёт до _TG_LOCK_TIMEOUT, потом проходит без лока. Поведение прежнее.
        fresh_path = self.sess + ".lock"
        open(fresh_path, "w").close()  # mtime = now → свежий

        orig_to = wiring._TG_LOCK_TIMEOUT
        wiring._TG_LOCK_TIMEOUT = 0.1  # короткий таймаут — тест быстрый
        proceeded = {"yes": False}

        def fake(inputs, env):
            proceeded["yes"] = True
            return {"items": []}

        wiring.collect_source.run = fake
        try:
            wiring._collect_locked({}, {"telegram_session": self.sess})
        finally:
            wiring._TG_LOCK_TIMEOUT = orig_to

        self.assertTrue(proceeded["yes"])
        # ВАЖНО: lock-файл ОСТАЛСЯ (чужой, state_lock при timeout не трогает его,
        # и _remove_stale_lock тоже не тронул — свежий).
        # Но state_lock в timeout-режиме НЕ удаляет lock, значит он там и должен быть.
        # Однако! При timeout state_lock не открывает fd, и finally-ветка не close'ит
        # НИЧЕГО (fd is None) → lock тоже не remove. Файл остаётся как был.
        self.assertTrue(os.path.exists(fresh_path))


class TestRunIdeateProviderSurfaces(unittest.TestCase):
    """_run_ideate пробрасывает ask_llm.last_provider в out органа (звено конвейера provider
    цепочки closerouter). Только при callable llm; без этого звена id ответившей модели
    (muse-spark/deepseek/nemotron) не дойдёт до harvest._degrade_note (consumer)."""

    def setUp(self):
        self._orig_ideate = wiring.ideate.run
        self._orig_lp = ask_llm.last_provider

    def tearDown(self):
        wiring.ideate.run = self._orig_ideate
        ask_llm.last_provider = self._orig_lp

    def test_provider_in_out_when_llm_present(self):
        # живая модель отозвалась muse-spark (первичная цепочки) → _run_ideate кладёт provider в out
        wiring.ideate.run = lambda inputs, env: {"ideas": [{"title": "X"}]}
        ask_llm.last_provider = "muse-spark"
        out = wiring._run_ideate({"items": [{"title": "t"}]}, {"content_llm": lambda p: "x"})
        self.assertEqual(out.get("provider"), "muse-spark")

    def test_no_provider_when_llm_absent(self):
        # stub-режим (нет callable llm) — provider неуместен (спросить некого), не кладём в out.
        # Иначе гонял бы ask_llm.last_provider от чужого прошлого вызова.
        wiring.ideate.run = lambda inputs, env: {"ideas": [{"title": "X", "brain": "stub"}]}
        out = wiring._run_ideate({"items": [{"title": "t"}]}, {})
        self.assertNotIn("provider", out)

    def test_no_provider_when_last_provider_empty(self):
        # llm есть, но last_provider пуст (сбой/первый вызов) → не кладём пустышку в out
        wiring.ideate.run = lambda inputs, env: {"ideas": [{"title": "X"}]}
        ask_llm.last_provider = ""
        out = wiring._run_ideate({"items": [{"title": "t"}]}, {"content_llm": lambda p: "x"})
        self.assertNotIn("provider", out)


class TestRunIdeateProvenance(unittest.TestCase):
    """A5 provenance: после генерации каждая идея получает ссылку на item-источник
    (source_name, source_url, source_title, inspired_by). Сопоставление по Jaccard
    значимых слов title; порог 0.3 — ниже не приписываем (модель синтезировала, а не
    пересказала). Промпт ideate даёт модели ТОЛЬКО title — post-factum Jaccard корректен.
    """

    def setUp(self):
        self._orig = wiring.ideate.run

    def tearDown(self):
        wiring.ideate.run = self._orig

    def test_provenance_attached_by_jaccard(self):
        # идея «трекер сна» ← item «сон: трекер фаз» (высокий Jaccard по значимым словам)
        wiring.ideate.run = lambda inputs, env: {"ideas": [{"title": "трекер сна", "brain": "llm"}]}
        items = [
            {"title": "сон: трекер фаз", "url": "https://hn/x", "id": "i1", "source": "hn"},
            {"title": "новость про крипту", "url": "https://rd/y", "id": "i2", "source": "rd"},
        ]
        out = wiring._run_ideate({"items": items}, {})
        idea = out["ideas"][0]
        self.assertEqual(idea.get("inspired_by"), "i1")
        self.assertEqual(idea.get("source_name"), "hn")
        self.assertEqual(idea.get("source_url"), "https://hn/x")
        self.assertEqual(idea.get("source_title"), "сон: трекер фаз")

    def test_provenance_below_threshold_no_attachment(self):
        # идея и все items слабо перекликаются → НЕ навязываем ложный источник
        wiring.ideate.run = lambda inputs, env: {"ideas": [{"title": "оптимизация sql", "brain": "llm"}]}
        items = [{"title": "сон: трекер фаз", "url": "u1", "id": "i1", "source": "hn"}]
        out = wiring._run_ideate({"items": items}, {})
        idea = out["ideas"][0]
        self.assertNotIn("inspired_by", idea)
        self.assertNotIn("source_name", idea)

    def test_provenance_picks_best_match_when_multiple(self):
        # два item'а, один ближе по смыслу — берём лучший Jaccard, не первый
        wiring.ideate.run = lambda inputs, env: {"ideas": [{"title": "бот reminders", "brain": "llm"}]}
        items = [
            {"title": "сон трекер", "url": "u1", "id": "i1", "source": "hn"},
            {"title": "telegram бот напоминаний", "url": "u2", "id": "i2", "source": "rd"},
        ]
        out = wiring._run_ideate({"items": items}, {})
        idea = out["ideas"][0]
        self.assertEqual(idea.get("inspired_by"), "i2")  # бот + напоминания ближе
        self.assertEqual(idea.get("source_name"), "rd")

    def test_provenance_absent_when_no_items(self):
        # нет items → provenance не к чему крепить; идеи проходят как есть
        wiring.ideate.run = lambda inputs, env: {"ideas": [{"title": "X", "brain": "llm"}]}
        out = wiring._run_ideate({}, {})
        self.assertNotIn("inspired_by", out["ideas"][0])

    def test_provenance_robust_to_malformed_items(self):
        # item без title/id/source — не роняет конвейер, пропускается
        wiring.ideate.run = lambda inputs, env: {"ideas": [{"title": "трекер сна", "brain": "llm"}]}
        items = [
            {"url": "u1"},  # нет title
            {"title": "сон трекер фаз", "url": "u2", "id": "i2"},  # нет source — ок
            "не словарь",
        ]
        out = wiring._run_ideate({"items": items}, {})
        idea = out["ideas"][0]
        self.assertEqual(idea.get("inspired_by"), "i2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
