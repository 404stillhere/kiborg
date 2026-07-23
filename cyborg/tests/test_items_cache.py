"""Тесты A6 cache_check — фильтр items автосбора по SHA256(title), TTL 30 мин.

Автосбор гоняет каждые ~30 мин. Если item уже пришёл в одном из последних 3 прогонов,
повторно его не отдавать генератору — экономим LLM-вызовы на заголовках, которые юзер
уже видел (в виде идей или в виде stub). Ручной run.py НЕ фильтрует (решение юзера —
жмёшь кнопку, хочешь идей сейчас).
"""

import os
import unittest

import items_cache


class TestItemsCacheFilter(unittest.TestCase):
    """Фильтрация items по свежести заголовка. Только stdlib, файл-персист атомарно."""

    def setUp(self):
        self._orig_path = items_cache.PATH
        self.tmp = os.path.join(self.tmp_dir(), "ic_test.json")
        items_cache.PATH = self.tmp
        self._cleanup()

    def tearDown(self):
        items_cache.PATH = self._orig_path
        self._cleanup()

    def tmp_dir(self):
        import tempfile

        d = os.path.join(tempfile.gettempdir(), "kiborg_ic_tests")
        os.makedirs(d, exist_ok=True)
        return d

    def _cleanup(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def test_fresh_titles_pass_through(self):
        # все items новые — ни один не отфильтрован
        items = [{"title": "новость А", "id": "1"}, {"title": "новость Б", "id": "2"}]
        out = items_cache.filter_fresh(items)
        self.assertEqual([i["id"] for i in out], ["1", "2"])

    def test_recently_seen_title_filtered(self):
        # заголовок пришёл в прошлом прогоне (записан в кэш) → сейчас отфильтрован
        items_cache.mark_seen([{"title": "повтор", "id": "x"}])
        items = [{"title": "повтор", "id": "1"}, {"title": "новое", "id": "2"}]
        out = items_cache.filter_fresh(items)
        self.assertEqual([i["id"] for i in out], ["2"])  # «повтор» отрезан

    def test_ttl_expiry_unblocks_title(self):
        # TTL 30 мин: заголовок старше 30 мин снова проходит (лента могла обновиться смыслом)
        items_cache.mark_seen([{"title": "протухшее", "id": "x"}])
        # подделываем старую ts — двигаем все записи в кэше назад на 31 мин
        items_cache._age_out_backdate(31 * 60)
        items = [{"title": "протухшее", "id": "1"}]
        out = items_cache.filter_fresh(items)
        self.assertEqual([i["id"] for i in out], ["1"])  # TTL истёк — снова свежий

    def test_only_last_three_runs_kept(self):
        # кэш хранит только последние 3 прогона (старые записи выметаются): 4-й прогон
        # с новым набором выталкивает самый старый, и тот заголовок снова «свежий»
        for i in range(4):
            items_cache.mark_seen([{"title": f"прогон{i}", "id": str(i)}])
        # после 4 прогонов «прогон0» должен быть вытолкнут (только 3 последних в кэше)
        out = items_cache.filter_fresh([{"title": "прогон0", "id": "old"}])
        self.assertEqual([i["id"] for i in out], ["old"])  # снова проходит

    def test_malformed_items_pass_through(self):
        # item без title/not-dict — не роняет фильтр, проходит как есть (лучше лишний
        # прогон чем упавшая генерация)
        items = [{"id": "1"}, "не словарь", {"title": "ок", "id": "3"}]
        out = items_cache.filter_fresh(items)
        ids = [i.get("id") for i in out if isinstance(i, dict)]
        self.assertIn("1", ids)
        self.assertIn("3", ids)

    def test_empty_or_no_items_returns_empty(self):
        self.assertEqual(items_cache.filter_fresh([]), [])
        self.assertEqual(items_cache.filter_fresh(None), [])

    def test_corrupt_cache_file_treated_as_empty(self):
        # битый JSON в кэше — не роняет фильтр, всё считается свежим
        with open(self.tmp, "w", encoding="utf-8") as f:
            f.write("{не валидный json")
        items = [{"title": "после-сбоя", "id": "1"}]
        out = items_cache.filter_fresh(items)
        self.assertEqual([i["id"] for i in out], ["1"])

    def test_mark_seen_idempotent(self):
        # повторная запись того же заголовка не плодит дубли в кэше
        items_cache.mark_seen([{"title": "дубль", "id": "1"}])
        items_cache.mark_seen([{"title": "дубль", "id": "1"}])
        items = [{"title": "дубль", "id": "1"}]
        out = items_cache.filter_fresh(items)
        self.assertEqual(out, [])  # одна запись, но фильтрует


if __name__ == "__main__":
    unittest.main(verbosity=2)
