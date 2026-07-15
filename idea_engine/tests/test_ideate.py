"""Тест парсера ideate: терпим к формату модели (массив Gemini / JSONL / мусор),
падение на stub при непарсибельном, ценник по умолчанию."""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import ideate  # noqa: E402

ITEMS = [{"title": "A tiny CRDT in 200 lines"}, {"title": "QuadRF sees WiFi through walls"}]

# как реально отвечает Gemini — pretty-printed JSON-массив
ARRAY = """[
  {
    "title": "CRDT-песочница",
    "why": "визуализировать слияние правок",
    "effort": "средне"
  },
  {
    "title": "RF-детектор аномалий",
    "why": "мониторить домашний эфир",
    "effort": "легко"
  }
]"""

JSONL = ('{"title":"Идея A","why":"раз","effort":"легко"}\n'
         '{"title":"Идея B","why":"два","effort":"тяжело"}')

# модель обернула список в объект — частый ответ рассуждающих моделей на JSON-схему
WRAPPED = ('{"ideas":[{"title":"Идея A","why":"раз","effort":"легко"},'
           '{"title":"Идея B","why":"два","effort":"средне"}]}')


class TestIdeateParse(unittest.TestCase):
    def test_parses_gemini_array(self):
        out = ideate.run({"items": ITEMS}, {"k": 3, "llm": lambda p: ARRAY})
        ideas = out["ideas"]
        self.assertEqual(len(ideas), 2)
        self.assertEqual(ideas[0]["brain"], "llm")          # не свалились на stub
        self.assertEqual(ideas[0]["title"], "CRDT-песочница")
        self.assertEqual(ideas[1]["effort"], "легко")

    def test_parses_jsonl(self):
        out = ideate.run({"items": ITEMS}, {"k": 3, "llm": lambda p: JSONL})
        self.assertEqual([i["title"] for i in out["ideas"]], ["Идея A", "Идея B"])
        self.assertTrue(all(i["brain"] == "llm" for i in out["ideas"]))

    def test_parses_wrapped_ideas_object(self):
        # модель обернула список в объект {"ideas":[...]} — достаём реальные идеи,
        # а не пустую карточку-обёртку (и не глушим фолбэк на stub пустышкой)
        out = ideate.run({"items": ITEMS}, {"k": 3, "llm": lambda p: WRAPPED})
        self.assertEqual([i["title"] for i in out["ideas"]], ["Идея A", "Идея B"])
        self.assertTrue(all(i["brain"] == "llm" for i in out["ideas"]))
        self.assertTrue(all(i["title"] for i in out["ideas"]))   # карточки не пустые

    def test_respects_k_limit(self):
        out = ideate.run({"items": ITEMS}, {"k": 1, "llm": lambda p: ARRAY})
        self.assertEqual(len(out["ideas"]), 1)

    def test_unparseable_falls_to_stub(self):
        out = ideate.run({"items": ITEMS}, {"k": 2, "llm": lambda p: "извините, не могу"})
        self.assertTrue(all(i["brain"] == "stub" for i in out["ideas"]))
        self.assertEqual(len(out["ideas"]), 2)

    def test_no_llm_is_stub(self):
        out = ideate.run({"items": ITEMS}, {"k": 2})
        self.assertTrue(all(i["brain"] == "stub" for i in out["ideas"]))

    def test_direction_steers_prompt(self):
        # руль темы попадает в запрос модели (генератор гнёт идеи в направление)
        seen = {}
        ideate.run({"items": ITEMS},
                   {"k": 2, "direction": "железки", "llm": lambda p: seen.setdefault("p", p) or ARRAY})
        self.assertIn("железки", seen["p"])
        self.assertIn("НАПРАВЛЕНИЕ", seen["p"])

    def test_no_direction_no_steer(self):
        # без направления руль в запрос НЕ вставляется (поведение как раньше)
        seen = {}
        ideate.run({"items": ITEMS}, {"k": 2, "llm": lambda p: seen.setdefault("p", p) or ARRAY})
        self.assertNotIn("НАПРАВЛЕНИЕ", seen["p"])

    def test_on_progress_emits_before_generation(self):
        # опц. суб-прогресс: орган шлёт «генерирую N идей» перед вызовом модели (пульт не молчит)
        msgs = []
        ideate.run({"items": ITEMS}, {"k": 3, "llm": lambda p: ARRAY, "on_progress": msgs.append})
        self.assertIn("генерирую 3 идей", msgs)

    def test_on_progress_optional_no_llm(self):
        # без llm (stub) колбэк не зовётся и ничего не ломается; не-callable — тоже безопасно
        msgs = []
        ideate.run({"items": ITEMS}, {"k": 2, "on_progress": msgs.append})   # stub-путь
        self.assertEqual(msgs, [])
        self.assertEqual(len(ideate.run({"items": ITEMS}, {"k": 2, "llm": lambda p: ARRAY,
                                                           "on_progress": "nope"})["ideas"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
