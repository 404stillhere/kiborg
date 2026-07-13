"""Тест органа readability_gate: балл читаемости, переписывание ТОЛЬКО ниже порога,
количество не меняем (идею не теряем), passthrough без llm / при сбое парса."""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import readability_gate  # noqa: E402

IDEAS = [
    {"title": "Мутная", "why": "На базе идеи говорящего ошейника — звуки (щенка в пути)", "brain": "llm"},
    {"title": "Ясная", "why": "Ошейник с микрофоном шлёт хозяину, что за лай — тревога или чужой", "brain": "llm"},
]


def _llm(score_json, why_new='{"why":"Переписано понятно с нуля"}', rescore='{"scores":[3,9]}'):
    """Фейк-llm. На БАТЧ-оценку — score_json; на ПАРНУЮ ре-оценку правки (в промпте виден
    переписанный текст) — rescore=[балл_старого, балл_нового]; на переписывание — why_new."""
    marker = "Переписано понятно с нуля"
    def fn(prompt):
        if '"scores"' in prompt:
            return rescore if marker in prompt else score_json
        return why_new
    return fn


class TestReadabilityGate(unittest.TestCase):
    def test_rewrites_below_threshold_only(self):
        # мутную (3) переписали, в паре новый выше старого (9>3) -> правку берём
        out = readability_gate.run({"ideas_best": IDEAS}, {"llm": _llm('{"scores":[3,9]}'), "min_score": 7})
        ideas = out["ideas_polished"]
        self.assertEqual(len(ideas), 2)                       # количество не изменилось
        self.assertTrue(ideas[0].get("read_fixed"))           # мутную (3) переписали
        self.assertEqual(ideas[0]["why"], "Переписано понятно с нуля")
        self.assertNotIn("read_fixed", ideas[1])              # ясную (9) не трогали
        self.assertEqual(ideas[1]["why"], IDEAS[1]["why"])
        self.assertEqual(ideas[0]["read_score"], 9)           # балл отражает УЛУЧШЕННЫЙ финальный текст
        self.assertEqual(ideas[1]["read_score"], 9)

    def test_rewrite_reverted_when_not_better(self):
        # мутную (3) переписали, но в паре новый НЕ выше старого (2<3) -> откат к старому why
        out = readability_gate.run({"ideas_best": IDEAS},
                                   {"llm": _llm('{"scores":[3,9]}', rescore='{"scores":[3,2]}'), "min_score": 7})
        ideas = out["ideas_polished"]
        self.assertEqual(ideas[0]["why"], IDEAS[0]["why"])    # правку отбросили, старое не тронуто
        self.assertNotIn("read_fixed", ideas[0])
        self.assertEqual(ideas[0]["read_score"], 3)           # балл остался исходный

    def test_no_fix_when_rewrite_returns_identical(self):
        # редактор вернул текст, идентичный старому -> правку не берём, read_fixed не ставим,
        # даже если бы пара дала рост (ре-оценки тут вообще не происходит — текст не изменился)
        same = '{"why":"%s"}' % IDEAS[0]["why"]
        out = readability_gate.run({"ideas_best": IDEAS},
                                   {"llm": _llm('{"scores":[3,9]}', why_new=same), "min_score": 7})
        ideas = out["ideas_polished"]
        self.assertEqual(ideas[0]["why"], IDEAS[0]["why"])
        self.assertNotIn("read_fixed", ideas[0])
        self.assertEqual(ideas[0]["read_score"], 3)

    def test_keeps_old_why_when_rewrite_fails(self):
        # судья говорит «мутно» (2), но редактор вернул мусор -> старое описание остаётся
        out = readability_gate.run({"ideas_best": IDEAS},
                                   {"llm": _llm('{"scores":[2,8]}', why_new="извините не могу"), "min_score": 7})
        ideas = out["ideas_polished"]
        self.assertEqual(ideas[0]["why"], IDEAS[0]["why"])    # не потеряли, не испортили
        self.assertNotIn("read_fixed", ideas[0])
        self.assertEqual(ideas[0]["read_score"], 2)

    def test_no_llm_passthrough(self):
        out = readability_gate.run({"ideas_best": IDEAS}, {})
        self.assertEqual(out["ideas_polished"], IDEAS)        # без модели — как есть

    def test_unparseable_scores_passthrough(self):
        out = readability_gate.run({"ideas_best": IDEAS}, {"llm": _llm("не число"), "min_score": 7})
        ideas = out["ideas_polished"]
        self.assertEqual(len(ideas), 2)
        self.assertNotIn("read_score", ideas[0])              # балл не распарсили — не метим
        self.assertEqual(ideas[0]["why"], IDEAS[0]["why"])    # и не переписываем

    def test_empty_and_key_fallback(self):
        self.assertEqual(readability_gate.run({}, {"llm": _llm('{"scores":[5]}')})["ideas_polished"], [])
        # читает и из ideas (не только ideas_best)
        out = readability_gate.run({"ideas": [IDEAS[0]]}, {"llm": _llm('{"scores":[9]}'), "min_score": 7})
        self.assertEqual(len(out["ideas_polished"]), 1)
        self.assertEqual(out["ideas_polished"][0]["read_score"], 9)

    def test_always_produces_key(self):
        # даже без правок ключ ideas_polished есть всегда (иначе конвейер встанет)
        for env in ({}, {"llm": _llm('{"scores":[9,9]}')}, {"llm": _llm("мусор")}):
            self.assertIn("ideas_polished", readability_gate.run({"ideas_best": IDEAS}, env))


if __name__ == "__main__":
    unittest.main(verbosity=2)
