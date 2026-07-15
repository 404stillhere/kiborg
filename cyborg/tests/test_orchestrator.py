"""Тесты оркестратора на фейковых органах (без сети): дата-флоу, роутер-подмножество,
падение органа (перепланирование), прод-гейт, подключение LLM-мозга."""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import Organ  # noqa: E402
from orchestrator import Cyborg  # noqa: E402
import router as R  # noqa: E402


def src(inputs, env):
    return {"items": [1, 2, 3]}


def xf(inputs, env):
    return {"ideas": ["a", "b"]} if inputs.get("items") else {"ideas": []}


def boom(inputs, env):
    raise ValueError("boom")


def base_organs():
    return [
        Organ("collect_source", "собрать свежие идеи источник", src, role="source",
              produces=["items"], tags=["идеи", "собрать", "свежие"]),
        Organ("ideate", "придумать идеи предложить", xf, role="transform",
              produces=["ideas"], consumes=["items"], tags=["идея", "идеи"]),
    ]


class TestOrchestrator(unittest.TestCase):
    def test_dataflow_source_then_transform_then_finish(self):
        out = Cyborg(base_organs(), max_steps=6).run("приноси свежие идеи", env={})
        self.assertEqual(out["deliverable"], "ideas")
        self.assertEqual(out["result"], ["a", "b"])
        names = [t.get("organ") for t in out["trace"] if t.get("organ")]
        self.assertEqual(names, ["collect_source", "ideate"])  # порядок дата-флоу
        self.assertEqual(out["trace"][-1].get("action"), "finish")

    def test_degraded_signal_surfaces_in_output(self):
        # root #1: degraded из источника выходит В ВЫХЛОП (не тонет в mem.data), а dropped_stub
        # без доставки = 0 (не падает). Так лог/пульт могут показать сбой, а не «доставлено N».
        def src_degraded(inputs, env):
            return {"items": [1, 2], "degraded": True}

        organs = [
            Organ("collect_source", "собрать свежие идеи источник", src_degraded, role="source",
                  produces=["items"], tags=["идеи", "собрать", "свежие"]),
            Organ("ideate", "придумать идеи предложить", xf, role="transform",
                  produces=["ideas"], consumes=["items"], tags=["идея", "идеи"]),
        ]
        out = Cyborg(organs, max_steps=6).run("приноси свежие идеи", env={})
        self.assertTrue(out["degraded"])
        self.assertEqual(out["dropped_stub"], 0)

    def test_router_selects_relevant_subset(self):
        many = base_organs() + [Organ("noise%d" % i, "нерелевантный шум",
                                       lambda i, e: {}, tags=["zzz"]) for i in range(12)]
        picked = R.route("приноси идеи", many, k=3)
        self.assertLessEqual(len(picked), 3)  # не все 14 — подмножество
        got = [o.name for o in picked]
        self.assertIn("collect_source", got)
        self.assertIn("ideate", got)

    def test_failing_organ_no_crash_not_retried(self):
        orgs = [Organ("bad", "собрать идеи источник", boom, role="source",
                      produces=["items"], tags=["идеи"])]
        out = Cyborg(orgs, max_steps=5).run("приноси идеи", env={})
        errs = [t for t in out["trace"] if t.get("error")]
        self.assertEqual(len(errs), 1)  # упал ОДИН раз, повторно не долбим (перепланирование)
        self.assertEqual(out["trace"][-1].get("action"), "finish")

    def test_safe_mode_gates_prod_organ(self):
        def prod(i, e):
            return {"ideas": ["LEAK"]}
        orgs = [Organ("proddy", "идеи", prod, role="source", produces=["ideas"],
                      tags=["идеи"], needs={"prod": True})]
        out = Cyborg(orgs, safe_mode=True, max_steps=4).run("приноси идеи", env={})
        self.assertIsNone(out["result"])  # прод-орган не запущен -> нет ideas
        self.assertTrue([t for t in out["trace"] if t.get("skipped")])

    def test_empty_source_no_spin(self):
        # RED-контрпример скептика: источник вернул пусто (сбой/ратлимит HN).
        # Раньше переизбирался 8 раз = 8 живых запросов. Теперь — РОВНО один и финиш.
        calls = {"n": 0}

        def empty_src(inputs, env):
            calls["n"] += 1
            return {"items": []}

        orgs = [Organ("collect_source", "собрать свежие идеи источник", empty_src,
                      role="source", produces=["items"], tags=["идеи", "собрать"])]
        out = Cyborg(orgs, max_steps=8).run("приноси идеи", env={})
        self.assertEqual(calls["n"], 1)  # не молотим сеть 8 раз
        self.assertEqual(out["trace"][-1].get("action"), "finish")
        self.assertIn(out["result"], (None, []))

    def test_empty_transform_finishes(self):
        def empty_xf(inputs, env):
            return {"ideas": []}

        orgs = [
            Organ("collect_source", "собрать идеи источник", src, role="source",
                  produces=["items"], tags=["идеи"]),
            Organ("ideate", "придумать идеи", empty_xf, role="transform",
                  produces=["ideas"], consumes=["items"], tags=["идеи"]),
        ]
        out = Cyborg(orgs, max_steps=8).run("приноси идеи", env={})
        names = [t.get("organ") for t in out["trace"] if t.get("organ")]
        self.assertEqual(names, ["collect_source", "ideate"])  # по разу каждый, не спин
        self.assertEqual(out["trace"][-1].get("action"), "finish")

    def test_no_progress_organ_blocked(self):
        calls = {"n": 0}

        def wrong(inputs, env):
            calls["n"] += 1
            return {"junk": 1}  # объявлял produces=['items'], а вернул чужой ключ

        orgs = [Organ("collect_source", "собрать идеи источник", wrong, role="source",
                      produces=["items"], tags=["идеи"])]
        out = Cyborg(orgs, max_steps=8).run("приноси идеи", env={})
        self.assertEqual(calls["n"], 1)  # отработал без своего produces -> blocked, не переизбор
        self.assertEqual(out["trace"][-1].get("action"), "finish")

    def test_terminal_deliverable_with_sink(self):
        # с sink-доставкой терминал цепи = 'delivered', киборг доводит идеи до конца
        def sink(inputs, env):
            return {"delivered": len(inputs.get("ideas") or [])}

        orgs = base_organs() + [
            Organ("deliver", "доставить идеи инбокс", sink, role="sink",
                  produces=["delivered"], consumes=["ideas"], tags=["доставить", "идеи"]),
        ]
        out = Cyborg(orgs, max_steps=6).run("приноси свежие идеи", env={})
        self.assertEqual(out["deliverable"], "delivered")  # терминал, не ideas
        names = [t.get("organ") for t in out["trace"] if t.get("organ")]
        self.assertEqual(names, ["collect_source", "ideate", "deliver"])
        self.assertEqual(out["result"], 2)

    def test_llm_brain_used_when_provided(self):
        calls = {"n": 0}

        def fake_llm(prompt):
            calls["n"] += 1
            return '{"finish": true}' if "в памяти: items" in prompt else '{"index": 0}'

        out = Cyborg(base_organs(), max_steps=4).run("приноси идеи", env={"llm": fake_llm})
        self.assertGreaterEqual(calls["n"], 2)  # LLM-мозг реально управлял циклом
        self.assertTrue(any(t.get("organ") == "collect_source" for t in out["trace"]))

    def test_on_step_callback_streams_live_progress(self):
        # живой прогресс: on_step зовётся start/done на каждый орган + finish в конце (для пульта,
        # чтобы долгий прогон не выглядел зависшим). Без колбэка (default None) поведение то же.
        events = []
        out = Cyborg(base_organs(), max_steps=6).run(
            "приноси свежие идеи", env={},
            on_step=lambda step, phase, name, why: events.append((phase, name)))
        phases = [e[0] for e in events]
        self.assertEqual(phases.count("start"), 2)          # два органа стартовали
        self.assertEqual(phases.count("done"), 2)           # и завершились
        self.assertEqual(phases[-1], "finish")              # цикл закрылся финишем
        # порядок: старт органа идёт ПЕРЕД его done (пользователь видит «сейчас работаю над X»)
        self.assertEqual(events[0], ("start", "collect_source"))
        self.assertEqual(events[1], ("done", "collect_source"))
        # и прогон отработал как обычно — колбэк ничего не сломал
        self.assertEqual(out["result"], ["a", "b"])

    def test_on_step_callback_failure_does_not_break_run(self):
        # сбой колбэка прогресса не роняет прогон (прогресс — удобство, не критичный путь)
        def bad_cb(step, phase, name, why):
            raise RuntimeError("callback boom")
        out = Cyborg(base_organs(), max_steps=6).run("приноси идеи", env={}, on_step=bad_cb)
        self.assertEqual(out["result"], ["a", "b"])         # прогон дошёл до результата

    def test_degraded_source_not_blocked(self):
        # источник ОТДАЛ данные через резерв (degraded_reason, НЕ error) — не блокировать, идеи текут
        def degraded_src(inputs, env):
            return {"items": [1, 2], "degraded": True, "degraded_reason": "net down"}
        orgs = [
            Organ("collect_source", "собрать идеи источник", degraded_src, role="source",
                  produces=["items"], tags=["идеи"]),
            Organ("ideate", "придумать идеи", xf, role="transform",
                  produces=["ideas"], consumes=["items"], tags=["идеи"]),
        ]
        out = Cyborg(orgs, max_steps=6).run("приноси идеи", env={})
        self.assertEqual(out["result"], ["a", "b"])         # идеи доставлены (источник не заблокирован)
        self.assertEqual([t for t in out["trace"] if t.get("error")], [])  # ноль ложных error


if __name__ == "__main__":
    unittest.main(verbosity=2)
