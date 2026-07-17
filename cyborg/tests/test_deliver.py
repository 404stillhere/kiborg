"""deliver-sink: фильтр болванок (brain='stub') в llm-режиме.

Пробел, который закрывает: deliver.run пишет в ЖИВОЙ state.json/inbox.md, поэтому его не гонял
ни один тест (test_pipeline_integration deliver-sink намеренно исключает). Контракт фильтра
(коммит 9fcded7 «доставлять болванки при полном отказе LLM/баланса»):
  • llm_mode И в партии есть ХОТЯ БЫ ОДНА brain='llm' → болванки = шум, отбрасываются;
  • llm_mode НО ВСЕ идеи — болванки (нет ни одной llm) → полный отказ LLM, деградируем:
    болванки лучше пустоты в инбоксе, доставляем как есть (раньше молчал);
  • нет ключа (stub-режим штатный) → болванки ожидаемы, доставляем как есть.
Прод-state изолирован через monkeypatch deliver._load_ie_run на фейковый модуль с tmp-путями
(реальный инбокс НЕ трогаем).
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

    def test_all_stub_degrade_when_no_llm_idea(self):
        # Ключ есть (llm_mode), НО в партии НЕТ ни одной brain='llm' — это полный отказ
        # LLM/баланса (402/сеть/пустой ответ). Деградируем: болванки лучше пустоты в инбоксе,
        # доставляем их как есть (коммит 9fcded7). dropped_stub=0 — фильтр не сработал.
        ideas = [{"title": "Идея по мотиву: A", "why": "x", "brain": "stub"},
                 {"title": "Идея по мотиву: B", "why": "y", "brain": "stub"}]
        out = deliver.run({"ideas_safe": ideas}, {"content_llm": lambda p: "x"})
        self.assertEqual(out["delivered"], 2)
        self.assertEqual(out["dropped_stub"], 0)
        self.assertEqual(len(self._open_titles()), 2)

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

    def test_dropped_dup_counts_rejected_duplicates(self):
        # две идеи-дубликата (Jaccard>=0.6 → store.add_idea отклоняет вторую как дубль).
        # Новое поведение deliver (незакоммиченная правка): счётчик dropped_dup отражает отказы.
        ideas = [{"title": "Трекер сна для разработчика", "why": "полезно", "brain": "llm"},
                 {"title": "Трекер сна для разработчика", "why": "полезно", "brain": "llm"}]
        out = deliver.run({"ideas_safe": ideas}, {"content_llm": lambda p: "x"})
        self.assertEqual(out["delivered"], 1)        # первая прошла
        self.assertEqual(out["dropped_dup"], 1)      # вторая — дубль, отклонена
        self.assertEqual(len(self._open_titles()), 1)

    def test_has_room_checked_before_add_when_inbox_full(self):
        # перестановка (незакоммиченная правка): has_room() теперь ВЫШЕ add_idea.
        # При полном инбоксе (cap достигнут) — break ДО add_idea, dropped_dup НЕ инкрементируется
        # (идея даже не доходит до проверки дубля). Симметрия с обратной тягой store.
        # Имитируем полный инбокс через CFG cap=1 и одну уже лежащую идею.
        deliver._load_ie_run = lambda: types.SimpleNamespace(
            STATE=self.state, INBOX=os.path.join(self.tmpdir, "inbox.md"),
            CFG={"cap": 1}, _write_inbox=lambda store: None)
        from store import Store
        store = Store(self.state, cap=1)
        store.add_idea({"title": "уже лежит в инбоксе", "why": "x", "kind": "new"})
        store.save()
        ideas = [{"title": "совсем другая новая идея", "why": "уникально", "brain": "llm"},
                 {"title": "ещё одна другая идея", "why": "тоже уникально", "brain": "llm"}]
        out = deliver.run({"ideas_safe": ideas}, {"content_llm": lambda p: "x"})
        self.assertEqual(out["delivered"], 0)        # инбокс полон (cap=1, 1 идея) → ничего не добавилось
        self.assertEqual(out["dropped_dup"], 0)      # идеи не дошли до проверки дубля (has_room break раньше)


if __name__ == "__main__":
    unittest.main(verbosity=2)
