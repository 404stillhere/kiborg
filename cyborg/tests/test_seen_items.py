"""Тесты трекера «уже видели» (по ID сырых items, не по тексту сгенерированных идей)."""
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import seen_items  # noqa: E402


class TestSeenItems(unittest.TestCase):
    def setUp(self):
        self._orig_path = seen_items.PATH
        self._tmp = tempfile.mkdtemp(prefix="seen_items_")
        seen_items.PATH = os.path.join(self._tmp, "seen_items.json")

    def tearDown(self):
        seen_items.PATH = self._orig_path

    def test_item_key_needs_id(self):
        self.assertIsNone(seen_items._item_key({"title": "no id"}))
        self.assertIsNone(seen_items._item_key({"title": "empty id", "id": ""}))
        self.assertEqual(seen_items._item_key({"source": "hn", "id": 42}), "hn:42")

    def test_filter_fresh_first_pass_keeps_everything(self):
        items = [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}]
        fresh = seen_items.filter_fresh(items)
        self.assertEqual(len(fresh), 2)

    def test_filter_fresh_second_pass_drops_already_seen(self):
        items = [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}]
        seen_items.filter_fresh(items)  # первый прогон — отмечает оба виденными
        more = [{"title": "A", "source": "hn", "id": 1},   # старый
                {"title": "C", "source": "hn", "id": 3}]   # новый
        fresh = seen_items.filter_fresh(more)
        self.assertEqual([it["title"] for it in fresh], ["C"])

    def test_items_without_id_always_pass_through(self):
        items = [{"title": "no id here"}]
        self.assertEqual(len(seen_items.filter_fresh(items)), 1)
        self.assertEqual(len(seen_items.filter_fresh(items)), 1)  # снова — не теряем сырьё

    def test_count_fresh_does_not_mutate(self):
        items = [{"title": "A", "source": "hn", "id": 1}]
        self.assertEqual(seen_items.count_fresh(items), 1)
        self.assertEqual(seen_items.count_fresh(items), 1)  # повторный count не отмечает виденным
        seen_items.filter_fresh(items)  # а вот filter_fresh — отмечает
        self.assertEqual(seen_items.count_fresh(items), 0)

    def test_persists_across_loads(self):
        seen_items.filter_fresh([{"title": "A", "source": "hn", "id": 1}])
        self.assertIn("hn:1", seen_items.load())
        # новый "процесс" — просто новый load() с тем же PATH
        self.assertIn("hn:1", seen_items.load())

    def test_different_sources_same_id_dont_collide(self):
        seen_items.filter_fresh([{"title": "A", "source": "hn", "id": 1}])
        fresh = seen_items.filter_fresh([{"title": "B", "source": "reddit", "id": 1}])
        self.assertEqual(len(fresh), 1)  # разные источники — разные ключи, не путаются


if __name__ == "__main__":
    unittest.main(verbosity=2)
