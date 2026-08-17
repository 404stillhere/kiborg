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

from wiring import build_organs, build_oracle_organs  # noqa: E402

IDEA_PATH = ["collect_source", "ideate", "rank_ideas", "readability_gate", "scrub_secrets", "deliver"]
ORACLE_PATH = ["oracle_scan", "oracle_plan", "deliver_oracle"]


class TestPipelineKeysChain(unittest.TestCase):
    """(1) Статика: produces каждой стадии реально покрывает consumes следующих, до терминала."""

    def setUp(self):
        self.organs = {o.name: o for o in build_organs()}

    def test_idea_path_consumes_are_all_produced_upstream(self):
        available = set()
        for name in IDEA_PATH:
            o = self.organs[name]
            for c in o.consumes:
                self.assertIn(c, available, f"{name} потребляет '{c}', которого выше по цепи никто не произвёл")
            available.update(o.produces)
        self.assertIn("delivered", available)  # терминал цепи достигнут

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

    def test_oracle_path_keys_chain(self):
        # третья ветка: oracle_scan -> oracle_plan -> deliver_oracle (режим Oracle),
        # тем же правилом — consume каждой стадии произведён вверх по цепи, до 'delivered'
        organs = {o.name: o for o in build_oracle_organs()}
        available = set()
        for name in ORACLE_PATH:
            o = organs[name]
            for c in o.consumes:
                self.assertIn(c, available, f"{name} потребляет '{c}', которого выше по цепи никто не произвёл")
            available.update(o.produces)
        self.assertIn("delivered", available)
        # прямые ассерты на стыки — ловят переименование ключа в одной стадии
        self.assertEqual(organs["oracle_scan"].produces, ["project_map"])
        self.assertEqual((organs["oracle_plan"].consumes, organs["oracle_plan"].produces), (["project_map"], ["plan"]))
        self.assertEqual(organs["deliver_oracle"].consumes, ["plan"])


class TestPipelineDataFlow(unittest.TestCase):
    """(2) Динамика: идея реально протекает сквозь РЕАЛЬНЫЕ трансформы на стабах (без сети/ключа).
    deliver-sink исключён (пишет в живой инбокс) — его стык проверен статически выше."""

    def setUp(self):
        self.organs = {o.name: o for o in build_organs()}

    def test_idea_survives_real_transforms_offline(self):
        blob = {
            "items": [
                {"title": "Локальный CRDT-движок синхронизации в 200 строк", "url": "", "id": "1", "source": "hn"},
                {"title": "Как гонять агентов без присмотра всю ночь", "url": "", "id": "2", "source": "hn"},
            ]
        }
        for name in ["ideate", "rank_ideas", "readability_gate", "scrub_secrets"]:
            out = self.organs[name].run(dict(blob), {})  # env без llm -> стаб/passthrough, РЕАЛЬНЫЕ _run_*
            self.assertIsInstance(out, dict, f"{name} вернул не dict")
            blob.update(out)
        # ключ каждой стадии появился (сквозная стыковка на живых данных)
        for k in ("ideas", "ideas_best", "ideas_polished", "ideas_safe"):
            self.assertIn(k, blob, f"стадия не произвела '{k}' — цепь порвалась")
        self.assertTrue(blob["ideas_safe"], "хотя бы одна идея должна дойти до вычищенных")
        # у дошедшей идеи есть суть (title/why) — не пустой каркас
        first = blob["ideas_safe"][0]
        self.assertTrue(first.get("title") or first.get("why"))

    def test_oracle_scan_real_transform_offline(self):
        # голова oracle-ветки на живых данных: tmp-проект -> реальный project_map
        # (plan/deliver_oracle требуют LLM и пишут на диск — их стык проверен статически)
        import tempfile

        organs = {o.name: o for o in build_oracle_organs()}
        with tempfile.TemporaryDirectory() as proj:
            with open(os.path.join(proj, "main.py"), "w", encoding="utf-8") as f:
                f.write("def main():\n    print('demo')\n\n\nif __name__ == '__main__':\n    main()\n")
            out = organs["oracle_scan"].run({}, {"oracle_project": proj})
            self.assertIsInstance(out, dict)
            self.assertIn("project_map", out, "oracle_scan не произвёл 'project_map' — цепь порвалась")
            self.assertTrue(out["project_map"], "карта проекта пуста на непустом проекте")


if __name__ == "__main__":
    unittest.main(verbosity=2)
