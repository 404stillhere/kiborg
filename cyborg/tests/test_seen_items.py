"""Тесты трекера «уже видели» (по ID сырых items, не по тексту сгенерированных идей).

Формат хранения (с 2026-07-21): dict[str, int] (ключ → ts), TTL=90 дней, cap=5000, files:*
стабилизированы хешем basename. Старые тесты (контракт filter_fresh/mark_seen/count_fresh/load)
остаются зелёными — формат под капотом, сигнатуры публичных функций не поменялись.
"""
import json
import os
import sys
import tempfile
import time
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

    def test_filter_fresh_mark_false_does_not_persist(self):
        # mark=False: только фильтрует, файл не трогает (пометка отложена до успешной генерации)
        items = [{"title": "A", "source": "hn", "id": 1}]
        self.assertEqual(len(seen_items.filter_fresh(items, mark=False)), 1)
        self.assertNotIn("hn:1", seen_items.load())                 # НЕ отмечено
        self.assertEqual(len(seen_items.filter_fresh(items, mark=False)), 1)  # всё ещё свежий

    def test_mark_seen_persists(self):
        items = [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}]
        seen_items.filter_fresh(items, mark=False)                  # не метит
        seen_items.mark_seen(items)                                 # метит явно
        self.assertIn("hn:1", seen_items.load())
        self.assertIn("hn:2", seen_items.load())
        self.assertEqual(seen_items.filter_fresh(items, mark=False), [])  # теперь всё виденное

    # --- новый формат (2026-07-21): dict[str,int], TTL, cap, files-хеш, миграция ---

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
        old_ts = seen_items._now() - (seen_items.TTL_DAYS + 5) * 86400
        with open(seen_items.PATH, "w", encoding="utf-8") as f:
            json.dump({"hn:ancient": old_ts}, f)
        # новый прогон mark_seen → _save должен выкинуть древнюю, оставить новую
        seen_items.mark_seen([{"source": "hn", "id": 2}])
        data = seen_items.load()
        self.assertNotIn("hn:ancient", data)        # просроченная выкинута
        self.assertIn("hn:2", data)                  # свежая осталась

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
            seen_items.mark_seen(items)              # 10 → должно обрезаться до 5
            data = seen_items.load()
            self.assertEqual(len(data), 5)
        finally:
            seen_items.MAX_RECORDS = orig_max

    def test_files_key_is_hashed_basename(self):
        # files:* — id=абсолютный путь. Ключ должен быть ХЕШОМ basename, а не самим путём
        # (перенос проекта не должен инвалидировать все files-ключи разом).
        import hashlib
        key = seen_items._item_key({"source": "files", "id": "M:\\projects\\kiborg\\notes.md"})
        expected = "files:" + hashlib.sha1(b"notes.md").hexdigest()[:12]
        self.assertEqual(key, expected)
        # в ключе нет абсолютного пути (нет M:\, нет слэшей) — структура каталогов не утекает
        self.assertNotIn("M:", key)
        self.assertNotIn("\\", key)
        self.assertNotIn("projects", key)

    def test_files_key_stable_across_path_move(self):
        # тот же файл, перенесённый в другой каталог (перенос проекта) → Тот ЖЕ ключ:
        # хеш берётся от basename, а не от полного пути. Раньше (старый формат) перенос
        # инвалидировал ВСЕ files-ключи разом → весь архив снова становился «свежим».
        k1 = seen_items._item_key({"source": "files", "id": "C:\\old\\proj\\notes.md"})
        k2 = seen_items._item_key({"source": "files", "id": "D:\\new\\location\\notes.md"})
        self.assertEqual(k1, k2)         # один basename → один ключ
        # а вот разные basename — разные ключи (не коллидируют)
        k3 = seen_items._item_key({"source": "files", "id": "C:\\old\\proj\\other.md"})
        self.assertNotEqual(k1, k3)

    def test_migrate_legacy_list_format(self):
        # старый формат (list[str], до 2026-07-21) должен мигрировать в dict[str,int]
        # при ближайшем load(): все ключи получают ts=сейчас (иначе TTL выкинул бы всё разом)
        with open(seen_items.PATH, "w", encoding="utf-8") as f:
            json.dump(["hn:1", "hn:2", "reddit:5"], f)
        data = seen_items.load()
        self.assertIsInstance(data, dict)
        self.assertEqual(set(data.keys()), {"hn:1", "hn:2", "reddit:5"})
        for v in data.values():
            self.assertIsInstance(v, int)
            self.assertGreater(v, 0)

    def test_migrate_normalizes_legacy_files_keys(self):
        # КРИТИЧНО: старые files:* ключи хранили ПОЛНЫЙ путь (files:M:\\...\\README.md). При
        # миграции они должны перехешироваться до basename — иначе в файле окажется два
        # формата одновременно (старые с путём + новые с хешем), и дедуп сломается: тот же
        # файл даст два разных ключа. Нормализация единая для list и dict исходников.
        import hashlib
        expected = "files:" + hashlib.sha1(b"README.md").hexdigest()[:12]
        # list-форма
        with open(seen_items.PATH, "w", encoding="utf-8") as f:
            json.dump(["files:M:\\projects\\kiborg\\README.md"], f)
        self.assertEqual(set(seen_items.load().keys()), {expected})
        # dict-форма (тоже может быть с legacy-ключами)
        with open(seen_items.PATH, "w", encoding="utf-8") as f:
            json.dump({"files:C:\\old\\proj\\notes.md": 12345}, f)
        expected_notes = "files:" + hashlib.sha1(b"notes.md").hexdigest()[:12]
        self.assertEqual(set(seen_items.load().keys()), {expected_notes})
        # уже-нормализованный files-хеш НЕ должен дважды хешироваться (idempotent)
        with open(seen_items.PATH, "w", encoding="utf-8") as f:
            json.dump({expected: 12345}, f)
        self.assertEqual(set(seen_items.load().keys()), {expected})

    def test_migrate_already_new_format_passes_through(self):
        # уже новый формат dict[str,int] проходит как есть
        ts = seen_items._now()
        with open(seen_items.PATH, "w", encoding="utf-8") as f:
            json.dump({"hn:1": ts, "hn:2": ts}, f)
        data = seen_items.load()
        self.assertEqual(data, {"hn:1": ts, "hn:2": ts})

    def test_migrate_garbage_returns_empty(self):
        # мусор в файле → пустой dict (не падаем, читаем как «ничего не видели»)
        with open(seen_items.PATH, "w", encoding="utf-8") as f:
            f.write("not json at all {{{")
        self.assertEqual(seen_items.load(), {})
        with open(seen_items.PATH, "w", encoding="utf-8") as f:
            f.write("42")
        self.assertEqual(seen_items.load(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
