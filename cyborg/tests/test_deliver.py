"""deliver-sink: болванки (brain='stub') при живом ключе (llm_mode) НЕ доставляются в инбокс.

Пробел, который закрывает: deliver.run пишет в ЖИВОЙ state.json/inbox.md, поэтому его не гонял
ни один тест (test_pipeline_integration deliver-sink намеренно исключает). При обрыве LLM ideate
падает на болванки brain='stub'; раньше deliver сажал их в инбокс как «идеи» (root fail-open:
прогон рапортует «доставлено N» на мусоре). Тут проверяем фильтр — прод-state изолирован через
monkeypatch deliver._load_ie_run на фейковый модуль с tmp-путями (реальный инбокс НЕ трогаем).
"""
import os
import sys
import tempfile
import types
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(os.path.dirname(BASE), "idea_engine"))

import deliver  # noqa: E402


class TestDeliverStubFilter(unittest.TestCase):
    def setUp(self):
        # изолируем прод-state: фейковый ie с tmp-путями, _write_inbox — no-op
        self.tmpdir = tempfile.mkdtemp()
        self.state = os.path.join(self.tmpdir, "state.json")
        fake_ie = types.SimpleNamespace(
            STATE=self.state,
            INBOX=os.path.join(self.tmpdir, "inbox.md"),
            CFG={"cap": 0},
            _write_inbox=lambda store: None,
        )
        self._orig = deliver._load_ie_run
        deliver._load_ie_run = lambda: fake_ie

    def tearDown(self):
        deliver._load_ie_run = self._orig

    def _open_titles(self):
        from store import Store
        return [i["title"] for i in Store(self.state, cap=0).open_ideas()]

    def test_stub_dropped_in_llm_mode(self):
        # ключ есть (content_llm callable) -> болванки brain='stub' = шум, не доставляем
        ideas = [{"title": "Идея по мотиву: A", "why": "x", "brain": "stub"},
                 {"title": "Идея по мотиву: B", "why": "y", "brain": "stub"}]
        out = deliver.run({"ideas_safe": ideas}, {"content_llm": lambda p: "x"})
        self.assertEqual(out["delivered"], 0)
        self.assertEqual(out["dropped_stub"], 2)
        self.assertEqual(self._open_titles(), [])

    def test_stub_kept_without_key(self):
        # ключа нет (stub-режим) -> болванки ожидаемы, доставляем как есть.
        # заголовки заведомо различны (без пересечения значимых слов) — дедуп не при делах
        ideas = [{"title": "Трекер сна для разработчика", "why": "x", "brain": "stub"},
                 {"title": "Генератор коммитов из мемов", "why": "y", "brain": "stub"}]
        out = deliver.run({"ideas_safe": ideas}, {})
        self.assertEqual(out["delivered"], 2)
        self.assertEqual(out["dropped_stub"], 0)
        self.assertEqual(len(self._open_titles()), 2)

    def test_real_ideas_pass_in_llm_mode(self):
        # живые идеи (brain='llm') при живом ключе доставляются
        ideas = [{"title": "Ночной агент обхода бэклога", "why": "полезно", "brain": "llm"},
                 {"title": "Панель метрик домашнего сервера", "why": "полезно", "brain": "llm"}]
        out = deliver.run({"ideas_safe": ideas}, {"content_llm": lambda p: "x"})
        self.assertEqual(out["delivered"], 2)
        self.assertEqual(out["dropped_stub"], 0)
        self.assertEqual(len(self._open_titles()), 2)

    def test_mixed_llm_mode_keeps_only_real(self):
        # смесь: живая идея проходит, болванка отсеивается
        ideas = [{"title": "Ночной агент обхода бэклога", "why": "полезно", "brain": "llm"},
                 {"title": "Идея по мотиву: мусор", "why": "z", "brain": "stub"}]
        out = deliver.run({"ideas_safe": ideas}, {"content_llm": lambda p: "x"})
        self.assertEqual(out["delivered"], 1)
        self.assertEqual(out["dropped_stub"], 1)
        self.assertEqual(self._open_titles(), ["Ночной агент обхода бэклога"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
