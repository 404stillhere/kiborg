"""Тест observe_sources — наблюдательный обход источников (рассказ от первого лица в stdout).

Мокаем harvest._harvest_env / seen_items.load / seen_items._item_key / collect_source.run и
глушим time.sleep — без сети, без файлов, без реальных пауз. Проверяем: обход активных
источников, счёт прочитано/новых, дедуп (видел -> мимо), degrade -> пропуск, краш источника
не роняет весь обход, неактивные источники молчат. Раньше модуль был без теста.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(os.path.dirname(BASE), "idea_engine"))

import observe_sources  # noqa: E402


class TestObserveSources(unittest.TestCase):
    def setUp(self):
        self._orig = (
            observe_sources.harvest._harvest_env,
            observe_sources.seen_items.load,
            observe_sources.seen_items._item_key,
            observe_sources.collect_source.run,
            observe_sources.time.sleep,
        )
        observe_sources.time.sleep = lambda *a, **k: None  # без реальных пауз
        observe_sources.seen_items._item_key = lambda it: it.get("id")

    def tearDown(self):
        (
            observe_sources.harvest._harvest_env,
            observe_sources.seen_items.load,
            observe_sources.seen_items._item_key,
            observe_sources.collect_source.run,
            observe_sources.time.sleep,
        ) = self._orig

    def _capture(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            observe_sources.main()
        return buf.getvalue()

    def test_walks_active_sources_counts_read_and_fresh(self):
        observe_sources.harvest._harvest_env = lambda: {"sources": ["hn", "reddit"]}
        observe_sources.seen_items.load = lambda: {"seen-1"}

        def fake_run(inputs, env):
            if env["source"] == "hn":
                return {
                    "degraded": False,
                    "items": [
                        {"title": "свежий пост", "id": "new-1"},
                        {"title": "старый пост", "id": "seen-1"},  # уже в снимке seen -> мимо
                    ],
                }
            return {"degraded": False, "items": [{"title": "reddit пост", "id": "new-2"}]}

        observe_sources.collect_source.run = fake_run

        out = self._capture()
        self.assertIn("Hacker News", out)
        self.assertIn("Reddit", out)
        self.assertIn("свежий пост", out)
        self.assertIn("уже видел", out)  # дедуп сработал на seen-1
        self.assertIn("новое", out)  # свежие помечены
        self.assertIn("прочитал 3", out)  # итог: 2 hn + 1 reddit
        self.assertIn("новых (не видел) 2", out)  # seen-1 не в счёт новых

    def test_degraded_source_skipped(self):
        observe_sources.harvest._harvest_env = lambda: {"sources": ["hn"]}
        observe_sources.seen_items.load = lambda: set()
        observe_sources.collect_source.run = lambda i, e: {"degraded": True, "degraded_reason": "403 IP"}
        out = self._capture()
        self.assertIn("пропускаю", out)
        self.assertIn("403 IP", out)
        self.assertIn("прочитал 0", out)

    def test_source_crash_does_not_abort_walk(self):
        observe_sources.harvest._harvest_env = lambda: {"sources": ["hn", "reddit"]}
        observe_sources.seen_items.load = lambda: set()

        def fake_run(i, e):
            if e["source"] == "hn":
                raise RuntimeError("boom")
            return {"degraded": False, "items": [{"title": "r", "id": "x"}]}

        observe_sources.collect_source.run = fake_run

        out = self._capture()
        self.assertIn("сорвался", out)  # hn упал
        self.assertIn("RuntimeError", out)
        self.assertIn("Reddit", out)  # обход продолжился до reddit
        self.assertIn("прочитал 1", out)  # reddit дал 1

    def test_only_active_sources_walked(self):
        observe_sources.harvest._harvest_env = lambda: {"sources": ["telegram"], "telegram_channels": ["@a"]}
        observe_sources.seen_items.load = lambda: set()
        observe_sources.collect_source.run = lambda i, e: {"degraded": False, "items": [{"title": "tg", "id": "t1"}]}
        out = self._capture()
        self.assertIn("Telegram", out)
        self.assertIn("@a", out)  # активные каналы показаны в шапке
        self.assertNotIn("Hacker News", out)  # hn не в active -> наблюдатель молчит

    def test_secret_in_title_scrubbed_before_display(self):
        # БЕЗОПАСНОСТЬ 2026-07-15: заголовок файла может нести секрет (фильтр _files неполон) —
        # observe чистит его ДО показа в консоли пульта (иначе секрет светился бы на экране).
        observe_sources.harvest._harvest_env = lambda: {"sources": ["files"], "files_paths": ["x"]}
        observe_sources.seen_items.load = lambda: set()
        observe_sources.collect_source.run = lambda i, e: {
            "degraded": False,
            "items": [{"title": "config.py — AQ.FAKEfake1234567890abcdefgh", "id": "f1"}],
        }
        out = self._capture()
        self.assertNotIn("AQ.FAKEfake1234567890abcdefgh", out)  # секрет НЕ в выводе
        self.assertIn("[REDACTED]", out)  # заменён скрабером


class TestObserveSourcesCoverage(unittest.TestCase):
    """Инвариант-страж (баг ревью 2026-07-14): 'files' выпал из обхода, т.к. был в collect_source
    ._SOURCES, но НЕ в ORDER/WHERE наблюдателя. Эти тесты краснеют, пока новый источник не добавлен
    в оба места — источник больше НЕ выпадет из кнопки «Наблюдать» молча."""

    def test_order_and_where_consistent(self):
        self.assertEqual(set(observe_sources.ORDER), set(observe_sources.WHERE))
        for name, val in observe_sources.WHERE.items():
            self.assertEqual(len(val), 2, f"{name}: WHERE = (человек, глагол)")

    def test_covers_all_real_sources(self):
        # наблюдатель обязан знать ВСЕ источники collect_source (иначе активный молча выпадет)
        self.assertEqual(set(observe_sources.ORDER), set(observe_sources.collect_source._SOURCES))

    def test_files_source_present(self):
        self.assertIn("files", observe_sources.ORDER)
        self.assertIn("files", observe_sources.WHERE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
