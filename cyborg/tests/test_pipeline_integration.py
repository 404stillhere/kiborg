"""Интеграция РЕАЛЬНОЙ цепочки `wiring.build_organs()` (не фикстура).

Пробел, который закрывает: `test_orchestrator` гоняет прогонщик Cyborg на УПРОЩЁННОМ
`base_organs()`; `test_wiring` тестирует каждую стадию ЮНИТОМ с прямыми входами. Ни то, ни
другое не проверяет, что 6 РЕАЛЬНЫХ стадий идея-пути стыкуются produces↔consumes сквозняком —
опечатка в ключе (readability даёт `ideas_polished`, scrub ждёт `ideas_polish`) прошла бы мимо.

Здесь: (1) статически — ключи реальной цепи образуют валидную цепочку до терминала `delivered`;
(2) динамически — данные реально протекают сквозь РЕАЛЬНЫЕ трансформы ideate→rank→readability→
scrub на стабах (без сети/ключа). `deliver`/`finish_sink` (пишут в живой инбокс) НЕ гоняем —
их вход `ideas_safe`/`nudge` проверяется статически в (1).
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(os.path.dirname(BASE), "idea_engine"))

from wiring import build_organs, build_harvest_organs  # noqa: E402

IDEA_PATH = ["collect_source", "ideate", "rank_ideas", "readability_gate", "scrub_secrets", "deliver"]


class TestPipelineKeysChain(unittest.TestCase):
    """(1) Статика: produces каждой стадии реально покрывает consumes следующих, до терминала."""

    def setUp(self):
        self.organs = {o.name: o for o in build_organs()}

    def test_idea_path_consumes_are_all_produced_upstream(self):
        available = set()
        for name in IDEA_PATH:
            o = self.organs[name]
            for c in o.consumes:
                self.assertIn(c, available,
                              f"{name} потребляет '{c}', которого выше по цепи никто не произвёл")
            available.update(o.produces)
        self.assertIn("delivered", available)          # терминал цепи достигнут

    def test_exact_junction_keys_regression(self):
        # прямые ассерты на стыки — ловят переименование ключа в одной стадии
        j = {n: (self.organs[n].consumes, self.organs[n].produces) for n in IDEA_PATH}
        self.assertEqual(j["collect_source"][1], ["items"])
        self.assertEqual(j["ideate"], (["items"], ["ideas"]))
        self.assertEqual(j["rank_ideas"], (["ideas"], ["ideas_best"]))
        self.assertEqual(j["readability_gate"], (["ideas_best"], ["ideas_polished"]))
        self.assertEqual(j["scrub_secrets"], (["ideas_polished"], ["ideas_safe"]))
        self.assertEqual(j["deliver"][0], ["ideas_safe"])

    def test_finish_path_keys_chain(self):
        # вторая ветка: finish_step -> finish_sink (доделать), тоже до 'delivered'
        fs, fk = self.organs["finish_step"], self.organs["finish_sink"]
        self.assertEqual(fs.produces, ["nudge"])
        self.assertEqual(fk.consumes, ["nudge"])
        self.assertIn("delivered", fk.produces)

    def test_harvest_path_keys_chain_to_stash(self):
        # АВТОНОМНЫЙ путь (build_harvest_organs): те же трансформы, но терминал — stash (копилка),
        # не deliver (инбокс). Стык scrub->stash (ideas_safe) отдельно от инбокс-пути — проверяем.
        h = {o.name: o for o in build_harvest_organs()}
        self.assertIn("stash_ideas", h)
        self.assertNotIn("deliver", h)                 # автономный сбор идёт в копилку, не в инбокс
        self.assertEqual(h["stash_ideas"].consumes, ["ideas_safe"])   # стыкуется с выходом scrub
        self.assertIn("delivered", h["stash_ideas"].produces)
        order = ["collect_source", "ideate", "rank_ideas", "readability_gate", "scrub_secrets", "stash_ideas"]
        available = set()
        for name in order:
            for c in h[name].consumes:
                self.assertIn(c, available, f"harvest: {name} потребляет '{c}', не произведённое выше по цепи")
            available.update(h[name].produces)
        self.assertIn("delivered", available)          # автономная цепь тоже доходит до терминала


class TestPipelineDataFlow(unittest.TestCase):
    """(2) Динамика: идея реально протекает сквозь РЕАЛЬНЫЕ трансформы на стабах (без сети/ключа).
    deliver-sink исключён (пишет в живой инбокс) — его стык проверен статически выше."""

    def setUp(self):
        self.organs = {o.name: o for o in build_organs()}

    def test_idea_survives_real_transforms_offline(self):
        blob = {"items": [
            {"title": "Локальный CRDT-движок синхронизации в 200 строк", "url": "", "id": "1", "source": "hn"},
            {"title": "Как гонять агентов без присмотра всю ночь", "url": "", "id": "2", "source": "hn"},
        ]}
        for name in ["ideate", "rank_ideas", "readability_gate", "scrub_secrets"]:
            out = self.organs[name].run(dict(blob), {})   # env без llm -> стаб/passthrough, РЕАЛЬНЫЕ _run_*
            self.assertIsInstance(out, dict, f"{name} вернул не dict")
            blob.update(out)
        # ключ каждой стадии появился (сквозная стыковка на живых данных)
        for k in ("ideas", "ideas_best", "ideas_polished", "ideas_safe"):
            self.assertIn(k, blob, f"стадия не произвела '{k}' — цепь порвалась")
        self.assertTrue(blob["ideas_safe"], "хотя бы одна идея должна дойти до вычищенных")
        # у дошедшей идеи есть суть (title/why) — не пустой каркас
        first = blob["ideas_safe"][0]
        self.assertTrue(first.get("title") or first.get("why"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
