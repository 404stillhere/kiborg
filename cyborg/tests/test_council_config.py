"""Тесты конфигурации совета советников (cyborg/council_config.py): какие советники активны
при отборе идей. Дефолт, сохранение, чистка до известных, атомарность записи, защита от
битого файла/типа. Раньше у модуля не было тестов — добавлены при выносе общего хелпера
_panel_config (дубль load/save скелета feeds/folders/direction/council_config)."""
import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import council_config  # noqa: E402


class TestCouncilConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="council_")
        self._orig = council_config.PATH
        council_config.PATH = os.path.join(self.tmp, "council.json")

    def tearDown(self):
        council_config.PATH = self._orig

    def test_default_when_no_file(self):
        # нет файла → все советники включены (DEFAULT_ENABLED), НЕ пусто
        d = council_config.load()
        self.assertEqual(d["all"], council_config.ALL_ADVISORS)
        self.assertEqual(d["enabled"], council_config.DEFAULT_ENABLED)

    def test_save_and_read(self):
        council_config.save(["rank_ideas", "orchestra"])
        self.assertEqual(council_config.load()["enabled"], ["rank_ideas", "orchestra"])

    def test_save_persists_to_disk_atomic(self):
        council_config.save(["ask_llm"])
        with open(council_config.PATH, encoding="utf-8") as f:
            json.load(f)                                       # валидный JSON на диске
        self.assertFalse(os.path.exists(council_config.PATH + ".tmp"))  # tmp убран
        self.assertEqual(council_config.load()["enabled"], ["ask_llm"])

    def test_unknown_advisors_dropped(self):
        # неизвестные имена молча выкидываются (только ALL_ADVISORS), порядок канонический
        council_config.save(["rank_ideas", "ghost", "", 42, "ask_llm"])
        self.assertEqual(council_config.load()["enabled"], ["rank_ideas", "ask_llm"])

    def test_non_list_save_becomes_empty(self):
        council_config.save("rank_ideas")     # не список → пусто (не падаем)
        self.assertEqual(council_config.load()["enabled"], [])

    def test_broken_file_falls_back_to_default(self):
        with open(council_config.PATH, "w", encoding="utf-8") as f:
            f.write("{ не json")
        self.assertEqual(council_config.load()["enabled"], council_config.DEFAULT_ENABLED)

    def test_missing_key_falls_back_to_default(self):
        # файл есть, но ключа enabled нет (или битого типа) → дефолт
        with open(council_config.PATH, "w", encoding="utf-8") as f:
            json.dump({"foo": "bar"}, f)
        self.assertEqual(council_config.load()["enabled"], council_config.DEFAULT_ENABLED)

    def test_is_enabled_helper(self):
        council_config.save(["rank_ideas"])
        self.assertTrue(council_config.is_enabled("rank_ideas"))
        self.assertFalse(council_config.is_enabled("ask_llm"))


if __name__ == "__main__":
    unittest.main()
