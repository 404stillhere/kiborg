"""Тест органа finish_sink (Левая рука): КЛАДЁТ nudge в инбокс в ДОРОЖКУ B.

Что фиксируем:
  1. АДРЕС: nudge попадает в дорожку B (store.data['finish']), а НЕ в дорожку A
     (store.data['ideas']) — «доделай» не съедает потолок новых идей.
  2. БЕЗОПАСНОСТЬ (на уровне конвейера): секрет из nudge вычищен ДО записи на диск —
     но чистит его ПЕЧЕНЬ (scrub_secrets) в wiring._run_finish_sink, а не сама рука.
  3. ЧИСТОТА МЕТАФОРЫ: рука сама НЕ чистит — вызванная напрямую, кладёт текст как есть.
     Каждый орган делает только своё: Печень фильтрует, Рука кладёт.
_load_ie_run монкипатчим на временную папку — не пишем в реальный инбокс/state.
"""
import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import brain  # noqa: E402
import finish_sink  # noqa: E402
import router as R  # noqa: E402
import wiring  # noqa: E402


class TestFinishSink(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="finish_sink_")
        state = os.path.join(self.tmp, "state.json")
        inbox = os.path.join(self.tmp, "inbox.md")

        class FakeIE:
            STATE = state
            INBOX = inbox
            CFG = {"cap": 3}

            @staticmethod
            def _write_inbox(store):  # инбокс-рендер не тестируем здесь — проверяем state
                pass

        self.fake = FakeIE
        self._orig = finish_sink._load_ie_run
        finish_sink._load_ie_run = lambda: FakeIE

    def tearDown(self):
        finish_sink._load_ie_run = self._orig

    def _state(self):
        with open(self.fake.STATE, encoding="utf-8") as f:
            return json.load(f)

    def test_routes_to_lane_B_not_A(self):
        res = finish_sink.run(
            {"nudge": {"title": "Доделать: x", "why": "почини путь", "folder": "x"}}, {})
        self.assertEqual(res["delivered"], 1)
        self.assertEqual(res["lane"], "B")
        d = self._state()
        self.assertIsNotNone(d["finish"])                    # дорожка B заполнена
        self.assertEqual(d["finish"]["title"], "Доделать: x")
        self.assertEqual(d["ideas"], [])                     # дорожка A НЕ тронута (потолок цел)

    def test_pipeline_scrubs_secret_on_disk(self):
        # БЕЗОПАСНОСТЬ на уровне конвейера: нудж идёт через wiring._run_finish_sink,
        # где Печень (scrub_secrets) чистит его ДО руки. На диск секрет не попадает.
        secret = "sk-ant-api03-DEADBEEFsecret0000000000000000"
        token = "12345678:AAHrealbottoken0123456789ABCDEFGHIJKLMNOP"
        wiring._run_finish_sink(
            {"nudge": {"title": "ротация " + token, "why": "ключ " + secret}}, {})
        blob = json.dumps(self._state(), ensure_ascii=False)
        self.assertNotIn(secret, blob)                       # sk-ant вычищен Печенью
        self.assertNotIn("AAHrealbottoken", blob)            # TG-токен вычищен Печенью
        self.assertIn("[REDACTED]", blob)

    def test_hand_alone_is_pure_placement(self):
        # ЧИСТОТА МЕТАФОРЫ: рука (finish_sink напрямую) сама НЕ чистит — кладёт как есть.
        # В живой системе рука всегда идёт после Печени (см. тест выше), поэтому «утечки»
        # тут нет; тест доказывает, что чистка — НЕ забота руки (её убрали из органа).
        token = "12345678:AAHrealbottoken0123456789ABCDEFGHIJKLMNOP"
        finish_sink.run({"nudge": {"title": "ротация " + token, "why": "как есть"}}, {})
        blob = json.dumps(self._state(), ensure_ascii=False)
        self.assertIn("AAHrealbottoken", blob)               # рука положила текст нетронутым
        self.assertNotIn("[REDACTED]", blob)                 # рука ничего не редактировала

    def test_write_is_state_locked(self):
        # СТРАЖ (pair_gap, нашла фабрика б-3 2026-07-15): дорожка B пишет state.json ПОД тем же
        # межпроцессным замком, что дорожка A (deliver.py:45) — иначе окно lost-update. Пиним, что
        # finish_sink берёт state_lock на НАШ state.json (раньше писал без замка = асимметрия).
        import store as _store
        calls = []
        orig = _store.state_lock

        def spy(path, *a, **k):
            calls.append(path)
            return orig(path, *a, **k)

        _store.state_lock = spy
        try:
            finish_sink.run({"nudge": {"title": "Доделать: y", "why": "z"}}, {})
        finally:
            _store.state_lock = orig
        self.assertIn(self.fake.STATE, calls)                # замок взят на нашем state.json

    def test_empty_nudge_noop_no_disk(self):
        for empty in (None, {}, "нет", []):
            res = finish_sink.run({"nudge": empty}, {})
            self.assertEqual(res["delivered"], 0)
        self.assertFalse(os.path.exists(self.fake.STATE))    # диск не тронут вовсе

    def test_wired_and_terminal_closed(self):
        organs = wiring.build_organs()
        sink = next(o for o in organs if o.name == "finish_sink")
        self.assertEqual(sink.role, "sink")
        self.assertEqual(sink.consumes, ["nudge"])
        self.assertEqual(sink.produces, ["delivered"])
        self.assertEqual(brain._terminal("nudge", organs), "delivered")  # pair_gap закрыт
        without = [o for o in organs if o.name != "finish_sink"]
        self.assertEqual(brain._terminal("nudge", without), "nudge")     # без sink застревал

    def test_ideas_goal_does_not_route_finish_sink(self):
        # скептик #4: цель «приноси идеи» НЕ должна тянуть finish_sink (ветка идей не задета),
        # а цель «доделать» — наоборот, тянет и finish_step, и finish_sink.
        organs = wiring.build_organs()
        ideas = [o.name for o in R.route("приноси свежие идеи", organs, k=5)]
        self.assertIn("collect_source", ideas)
        self.assertIn("ideate", ideas)
        self.assertIn("deliver", ideas)
        self.assertNotIn("finish_sink", ideas)
        finish = [o.name for o in R.route("доделать существующие проекты", organs, k=5)]
        self.assertIn("finish_step", finish)
        self.assertIn("finish_sink", finish)


if __name__ == "__main__":
    unittest.main(verbosity=2)
