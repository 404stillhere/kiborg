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


if __name__ == "__main__":
    unittest.main(verbosity=2)
