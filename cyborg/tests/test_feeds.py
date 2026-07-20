"""Тесты набора включённых лент (cyborg/feeds.py): дефолт, сохранение, чистка/канон-порядок,
пустой набор допустим, только-известные ленты, атомарность записи."""

import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import feeds  # noqa: E402


class TestFeeds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="feeds_")
        self._orig = feeds.PATH
        feeds.PATH = os.path.join(self.tmp, "feeds.json")

    def tearDown(self):
        feeds.PATH = self._orig

    def test_default_when_no_file(self):
        # нет файла → дефолт (как было захардкожено в harvest.SOURCES), НЕ пусто
        d = feeds.load()
        self.assertEqual(d["all"], feeds.ALL_FEEDS)
        self.assertEqual(d["enabled"], feeds.DEFAULT_FEEDS)
        self.assertEqual(feeds.enabled(), feeds.DEFAULT_FEEDS)

    def test_save_and_read(self):
        feeds.save(["hn", "telegram"])
        self.assertEqual(feeds.enabled(), ["hn", "telegram"])  # канон-порядок ALL_FEEDS

    def test_save_persists_to_disk_atomic(self):
        feeds.save(["reddit", "hn"])
        with open(feeds.PATH, encoding="utf-8") as f:
            json.load(f)  # валидный JSON на диске
        self.assertFalse(os.path.exists(feeds.PATH + ".tmp"))  # временный файл убран
        self.assertEqual(feeds.load()["enabled"], ["hn", "reddit"])

    def test_canonical_order_and_dedup(self):
        # ввод в произвольном порядке с дублями → канонический порядок ALL_FEEDS, без дублей
        feeds.save(["telegram", "hn", "telegram", "reddit"])
        self.assertEqual(feeds.enabled(), ["hn", "reddit", "telegram"])

    def test_unknown_feeds_dropped(self):
        # неизвестные имена молча выкидываются (защита от мусора из тела запроса)
        feeds.save(["hn", "myspace", "", 42, "gh_trending"])
        self.assertEqual(feeds.enabled(), ["hn", "gh_trending"])

    def test_empty_is_valid_all_off(self):
        # пустой набор — законный выбор юзера (все ленты выключены), НЕ подмена дефолтом
        feeds.save([])
        self.assertEqual(feeds.enabled(), [])

    def test_non_list_save_becomes_empty(self):
        feeds.save("hn")  # не список → пусто (не падаем)
        self.assertEqual(feeds.enabled(), [])

    def test_broken_file_falls_back_to_default(self):
        with open(feeds.PATH, "w", encoding="utf-8") as f:
            f.write("{ не json")
        self.assertEqual(feeds.enabled(), feeds.DEFAULT_FEEDS)  # битый файл → дефолт

    def test_missing_key_falls_back_to_default(self):
        # файл есть, но ключа enabled нет (или битого типа) → дефолт, а не пусто
        with open(feeds.PATH, "w", encoding="utf-8") as f:
            json.dump({"foo": "bar"}, f)
        self.assertEqual(feeds.enabled(), feeds.DEFAULT_FEEDS)

    def test_all_feeds_match_organ_sources_minus_files(self):
        # инвариант: тумблеры покрывают ровно ленты органа (collect_source._SOURCES) без 'files'
        from organs import collect_source

        organ = set(collect_source._SOURCES) - {"files"}
        self.assertEqual(set(feeds.ALL_FEEDS), organ)


if __name__ == "__main__":
    unittest.main()
