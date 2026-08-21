"""Тесты трекера «уже видели» (по ID сырых items, не по тексту сгенерированных идей).

Формат хранения: dict[str, int] (ключ → ts), TTL=90 дней, cap=5000. Новые files:* несут
версионированный id относительного пути + видимого заголовка, поэтому разные main.py не
схлопываются, а изменение сырья замечается. Старые basename-хеши мигрируются в legacy-пространство.
"""

import json
import os
import sys
import tempfile
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config  # noqa: E402
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
        more = [{"title": "A", "source": "hn", "id": 1}, {"title": "C", "source": "hn", "id": 3}]  # старый  # новый
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

    def test_filter_fresh_mark_false_does_not_persist(self):
        # mark=False: только фильтрует, файл не трогает (пометка отложена до успешной генерации)
        items = [{"title": "A", "source": "hn", "id": 1}]
        self.assertEqual(len(seen_items.filter_fresh(items, mark=False)), 1)
        self.assertNotIn("hn:1", seen_items.load())  # НЕ отмечено
        self.assertEqual(len(seen_items.filter_fresh(items, mark=False)), 1)  # всё ещё свежий

    def test_mark_seen_persists(self):
        items = [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}]
        seen_items.filter_fresh(items, mark=False)  # не метит
        seen_items.mark_seen(items)  # метит явно
        self.assertIn("hn:1", seen_items.load())
        self.assertIn("hn:2", seen_items.load())
        self.assertEqual(seen_items.filter_fresh(items, mark=False), [])  # теперь всё виденное

    # --- dict[str,int], TTL, cap, files-v2, миграция ---

    def test_load_returns_dict_with_ts(self):
        # формат dict[str,int]: ключ → ts последнего видения
        seen_items.filter_fresh([{"title": "A", "source": "hn", "id": 1}])
        data = seen_items.load()
        self.assertIn("hn:1", data)
        self.assertIsInstance(data["hn:1"], int)
        self.assertGreater(data["hn:1"], 0)

    def test_repeated_sighting_updates_ts(self):
        # повторное видение ОБНОВЛЯЕТ ts (иначе популярный пост, что всплывает часто,
        # оставался бы со старым ts и выкидывался по TTL раньше времени)
        seen_items.mark_seen([{"source": "hn", "id": 1}])
        old_ts = seen_items.load()["hn:1"]
        time.sleep(1.1)
        seen_items.mark_seen([{"source": "hn", "id": 1}])
        new_ts = seen_items.load()["hn:1"]
        self.assertGreater(new_ts, old_ts)

    def test_ttl_drops_expired_on_save(self):
        # запись старше TTL_DAYS выкидывается при ближайшем _save (файл сам себя чистит)
        seen_items.mark_seen([{"source": "hn", "id": 1}])
        # подделываем древнюю запись напрямую в файле
        old_ts = seen_items._now() - (seen_items.TTL_DAYS + 5) * config.SECONDS_PER_DAY
        with open(seen_items.PATH, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            json.dump({"hn:ancient": old_ts}, f)
        # новый прогон mark_seen → _save должен выкинуть древнюю, оставить новую
        seen_items.mark_seen([{"source": "hn", "id": 2}])
        data = seen_items.load()
        self.assertNotIn("hn:ancient", data)  # просроченная выкинута
        self.assertIn("hn:2", data)  # свежая осталась

    def test_ttl_keeps_fresh(self):
        # запись моложе TTL остаётся
        seen_items.mark_seen([{"source": "hn", "id": 1}])
        seen_items.mark_seen([{"source": "hn", "id": 2}])
        data = seen_items.load()
        self.assertIn("hn:1", data)
        self.assertIn("hn:2", data)

    def test_cap_trims_to_max(self):
        # жёсткий потолок MAX_RECORDS: при превышении остаются самые свежие
        orig_max = seen_items.MAX_RECORDS
        seen_items.MAX_RECORDS = 5
        try:
            items = [{"source": "hn", "id": i, "title": str(i)} for i in range(10)]
            seen_items.mark_seen(items)  # 10 → должно обрезаться до 5
            data = seen_items.load()
            self.assertEqual(len(data), 5)
        finally:
            seen_items.MAX_RECORDS = orig_max

    def test_files_v2_key_preserves_distinct_items(self):
        # Новый id уже не путь и не basename: генератор дал ему уникальную стабильную форму.
        k1 = seen_items._item_key({"source": "files", "id": "f2:aaa111"})
        k2 = seen_items._item_key({"source": "files", "id": "f2:bbb222"})
        self.assertEqual(k1, "files:f2:aaa111")
        self.assertEqual(k2, "files:f2:bbb222")
        self.assertNotEqual(k1, k2)

    def test_migrate_legacy_list_format(self):
        # старый формат (list[str], до 2026-07-21) должен мигрировать в dict[str,int]
        # при ближайшем load(): все ключи получают ts=сейчас (иначе TTL выкинул бы всё разом)
        with open(seen_items.PATH, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            json.dump(["hn:1", "hn:2", "reddit:5"], f)
        data = seen_items.load()
        self.assertIsInstance(data, dict)
        self.assertEqual(set(data.keys()), {"hn:1", "hn:2", "reddit:5"})
        for v in data.values():
            self.assertIsInstance(v, int)
            self.assertGreater(v, 0)

    def test_migrate_legacy_files_keys_keeps_them_separate_from_v2(self):
        # Старые пути и basename-хеши нельзя честно сопоставить с v2 (старый формат потерял
        # каталог), поэтому сохраняем их отдельно: новые точные ids не наследуют старые коллизии.
        import hashlib

        expected = "files:legacy-" + hashlib.sha1(b"README.md").hexdigest()[:12]
        # list-форма
        with open(seen_items.PATH, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            json.dump(["files:M:\\projects\\kiborg\\README.md"], f)
        self.assertEqual(set(seen_items.load().keys()), {expected})
        # dict-форма (тоже может быть с legacy-ключами)
        with open(seen_items.PATH, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            json.dump({"files:C:\\old\\proj\\notes.md": 12345}, f)
        expected_notes = "files:legacy-" + hashlib.sha1(b"notes.md").hexdigest()[:12]
        self.assertEqual(set(seen_items.load().keys()), {expected_notes})
        # уже мигрированный legacy и новый v2-ключи остаются как есть (idempotent)
        with open(seen_items.PATH, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            json.dump({expected: 12345, "files:f2:stable": 12345}, f)
        self.assertEqual(set(seen_items.load().keys()), {expected, "files:f2:stable"})

    def test_migrate_already_new_format_passes_through(self):
        # уже новый формат dict[str,int] проходит как есть
        ts = seen_items._now()
        with open(seen_items.PATH, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            json.dump({"hn:1": ts, "hn:2": ts}, f)
        data = seen_items.load()
        self.assertEqual(data, {"hn:1": ts, "hn:2": ts})

    def test_migrate_garbage_returns_empty(self):
        # мусор в файле → пустой dict (не падаем, читаем как «ничего не видели»)
        with open(seen_items.PATH, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            f.write("not json at all {{{")
        self.assertEqual(seen_items.load(), {})
        with open(seen_items.PATH, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            f.write("42")
        self.assertEqual(seen_items.load(), {})


class TestCrossDedup(unittest.TestCase):
    """cross_dedup — убирает кросс-источниковые дубли ВНУТРИ одного прогона (чистая функция,
    без персиста). Реальный кейс: один и тот же пост приходит с HN (item id) и Lobsters
    (short_id) → два разных source:id ключа в seen_items, оба проходят → LLM тратится на две
    похожие идеи. cross_dedup нормализует заголовок и оставляет ПЕРВОЕ вхождение, мимо дубли."""

    def test_drops_exact_dup_across_sources(self):
        items = [
            {"title": "John C. Dvorak has died", "source": "hn", "id": 1},
            {"title": "John C. Dvorak has died", "source": "lobsters", "id": "abc"},
            {"title": "SIMD tricks", "source": "hn", "id": 2},
        ]
        out = seen_items.cross_dedup(items)
        titles = [it["title"] for it in out]
        self.assertEqual(len(out), 2)  # Дворак-дубль схлопнут, SIMD остался
        self.assertEqual(titles.count("John C. Dvorak has died"), 1)

    def test_normalizes_case_and_punctuation(self):
        # «SIMD Tricks!» и «simd tricks» — один и тот же заголовок в разных регистрах/формате
        items = [
            {"title": "SIMD Tricks!", "source": "hn", "id": 1},
            {"title": "simd tricks", "source": "lobsters", "id": 2},
            {"title": "Unique post", "source": "hn", "id": 3},
        ]
        out = seen_items.cross_dedup(items)
        self.assertEqual(len(out), 2)

    def test_keeps_different_posts_same_source(self):
        # разные посты (даже одного источника) — НЕ дубли
        items = [
            {"title": "Post A", "source": "hn", "id": 1},
            {"title": "Post B", "source": "hn", "id": 2},
        ]
        self.assertEqual(len(seen_items.cross_dedup(items)), 2)

    def test_keeps_similar_but_not_identical(self):
        # «SIMD tricks» и «SIMD for collision» — РАЗНЫЕ посты (похожие слова, не дубли).
        # cross_dedup строгий: только ТОЧНОЕ совпадение нормализованной строки, не Jaccard.
        items = [
            {"title": "SIMD tricks", "source": "hn", "id": 1},
            {"title": "SIMD for collision", "source": "lobsters", "id": 2},
        ]
        self.assertEqual(len(seen_items.cross_dedup(items)), 2)

    def test_preserves_first_occurrence_order(self):
        items = [
            {"title": "Dup", "source": "lobsters", "id": "first"},
            {"title": "Dup", "source": "hn", "id": "second"},
        ]
        out = seen_items.cross_dedup(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "first")  # первое вхождение выигрывает

    def test_empty_or_no_title_passes_through(self):
        # без заголовка / пустой список — не краш, корректный пропуск
        self.assertEqual(seen_items.cross_dedup([]), [])
        out = seen_items.cross_dedup([{"title": "", "source": "hn", "id": 1}])
        self.assertEqual(len(out), 1)  # пустой title — пропускаем как есть (не дедупим)

    def test_non_list_input_returns_empty(self):
        self.assertEqual(seen_items.cross_dedup(None), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
