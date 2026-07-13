"""Тесты ядра: потолок, обратная тяга, разбор освобождает место, режим A<->B, статусы."""
import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from store import Store, OPEN, TAKE, LATER, TRASH, _SEEN_CAP  # noqa: E402
import run  # noqa: E402


def _idea(title="x"):
    return {"title": title, "why": "w", "effort": "средне", "brain": "stub", "kind": "new"}


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "state.json")

    def test_cap_enforced(self):
        s = Store(self.path, cap=3)
        self.assertTrue(s.add_idea(_idea("1")))
        self.assertTrue(s.add_idea(_idea("2")))
        self.assertTrue(s.add_idea(_idea("3")))
        # четвёртая не влезает — обратная тяга
        self.assertFalse(s.has_room())
        self.assertFalse(s.add_idea(_idea("4")))
        self.assertEqual(len(s.open_ideas()), 3)

    def test_clearing_frees_room(self):
        s = Store(self.path, cap=3)
        for i in range(3):
            s.add_idea(_idea(str(i)))
        self.assertFalse(s.has_room())
        s.set_status(1, TAKE)          # разобрал одну
        self.assertTrue(s.has_room())  # место освободилось
        self.assertTrue(s.add_idea(_idea("new")))
        self.assertEqual(len(s.open_ideas()), 3)

    def test_status_transitions(self):
        s = Store(self.path, cap=3)
        s.add_idea(_idea("a"))
        self.assertTrue(s.set_status(1, LATER))
        self.assertFalse(s.set_status(999, TAKE))  # нет такой
        with self.assertRaises(ValueError):
            s.set_status(1, "bogus")
        self.assertEqual(s.cleared_count(), 1)

    def test_idea_cannot_forge_status(self):
        s = Store(self.path, cap=3)
        bad = _idea("z")
        bad["status"] = "take"  # попытка проскочить мимо потолка
        s.add_idea(bad)
        self.assertEqual(len(s.open_ideas()), 1)  # всё равно OPEN

    def test_idea_cannot_forge_id(self):
        s = Store(self.path, cap=3)
        s.add_idea(_idea("first"))          # id=1
        forged = _idea("evil")
        forged["id"] = 1                     # попытка навязать чужой id
        s.add_idea(forged)                   # id должен стать 2, не 1
        ids = sorted(i["id"] for i in s.data["ideas"])
        self.assertEqual(ids, [1, 2])

    def test_reopen_cannot_break_cap(self):
        # контрпример скептика: разбор + добор + переоткрытие сверх потолка
        s = Store(self.path, cap=3)
        for i in range(3):
            s.add_idea(_idea(str(i)))        # open=3, полно
        self.assertTrue(s.set_status(1, TAKE))   # open=2
        self.assertTrue(s.add_idea(_idea("new")))  # open=3, снова полно
        # попытка переоткрыть разобранную #1 при полной дорожке -> отказ
        self.assertFalse(s.set_status(1, OPEN))
        self.assertEqual(len(s.open_ideas()), 3)  # потолок держит
        # CLI-путь тоже: 'open' не входит в разрешённые команды (проверяется в run._cli)

    def test_reopen_allowed_when_room(self):
        s = Store(self.path, cap=3)
        s.add_idea(_idea("a"))
        s.set_status(1, LATER)               # open=0, место есть
        self.assertTrue(s.set_status(1, OPEN))  # переоткрытие разрешено
        self.assertEqual(len(s.open_ideas()), 1)

    def test_persistence_roundtrip(self):
        s = Store(self.path, cap=2)
        s.add_idea(_idea("keep"))
        s.data["tick"] = 5
        s.save()
        s2 = Store(self.path, cap=2)
        self.assertEqual(s2.data["tick"], 5)
        self.assertEqual(len(s2.open_ideas()), 1)


class TestTickModes(unittest.TestCase):
    """Режимы A/B на подменённых органах — без сети."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        run.DATA = self.tmp
        run.STATE = os.path.join(self.tmp, "state.json")
        run.INBOX = os.path.join(self.tmp, "inbox.md")
        run.NOTIFY = os.path.join(self.tmp, "notify.md")
        # подменяем органы: без сети и без recon
        self._orig_collect = run.collect_source.run
        self._orig_ideate = run.ideate.run
        self._orig_finish = run.finish_step.run
        run.collect_source.run = lambda i, e: {"items": [{"title": "t"}], "source": "fake", "degraded": False}
        run.ideate.run = lambda i, e: {"ideas": [_idea("g1"), _idea("g2"), _idea("g3")]}
        run.finish_step.run = lambda i, e: {"nudge": {"title": "Доделать: X", "why": "шаг", "effort": "средне", "kind": "finish"}, "next_cursor": 1, "pool": 4}

    def tearDown(self):
        run.collect_source.run = self._orig_collect
        run.ideate.run = self._orig_ideate
        run.finish_step.run = self._orig_finish

    def test_fills_then_switches_to_B(self):
        s = Store(run.STATE, cap=3)
        info = run.tick(s)                 # дорожка пуста -> режим A, добьёт до 3
        self.assertEqual(info["mode"], "A")
        self.assertEqual(len(s.open_ideas()), 3)
        # следующий tick: полна -> режим B
        info2 = run.tick(s)
        self.assertEqual(info2["mode"], "B")
        self.assertTrue(info2["nudge"])
        self.assertIsNotNone(s.data["finish"])

    def test_B_back_to_A_after_clear(self):
        s = Store(run.STATE, cap=3)
        run.tick(s)                        # A -> 3 открытых
        run.tick(s)                        # B
        s.set_status(1, TRASH)             # разобрал одну
        s.save()
        info = run.tick(s)                 # снова есть место -> A
        self.assertEqual(info["mode"], "A")


class TestDedup(unittest.TestCase):
    """Память предложенного: похожую идею не добавляем повторно (дорожка A)."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "state.json")

    def test_duplicate_not_readded(self):
        s = Store(self.path, cap=3)
        self.assertTrue(s.add_idea(_idea("Умный трекер задач")))
        self.assertFalse(s.add_idea(_idea("умный трекер задач!")))   # тот же по смыслу
        self.assertEqual(len(s.open_ideas()), 1)

    def test_dup_remembered_after_trash(self):
        s = Store(self.path, cap=3)
        s.add_idea(_idea("RF детектор аномалий"))
        s.set_status(1, TRASH)                                       # разобрал (в мусор)
        self.assertTrue(s.has_room())
        self.assertFalse(s.add_idea(_idea("RF детектор аномалий")))  # всё равно не повторяем
        self.assertEqual(len(s.open_ideas()), 0)

    def test_distinct_ideas_pass(self):
        s = Store(self.path, cap=5)
        self.assertTrue(s.add_idea(_idea("Приложение для кофеен")))
        self.assertTrue(s.add_idea(_idea("Платформа для велопрокатов")))

    def test_different_ideas_sharing_common_words_pass(self):
        # скептик #4: разные идеи с общими служебными словами НЕ схлопывать
        s = Store(self.path, cap=5)
        self.assertTrue(s.add_idea(_idea("Бот для трекинга сна")))
        self.assertTrue(s.add_idea(_idea("Бот для трекинга финансов")))   # РАЗНАЯ тема
        self.assertTrue(s.add_idea(_idea("Бот для трекинга привычек")))    # и ещё одна
        self.assertEqual(len(s.open_ideas()), 3)

    def test_seen_persists_across_load(self):
        s = Store(self.path, cap=3)
        s.add_idea(_idea("Проект альфа"))
        s.save()
        s2 = Store(self.path, cap=3)
        self.assertFalse(s2.add_idea(_idea("проект альфа")))         # помнит между загрузками

    def test_seen_capped(self):
        # журнал предложенного не растёт бесконечно — помним последние _SEEN_CAP (скептик #5).
        # Вытеснение тестируем на МАЛЕНЬКОМ пороге (быстро) — логика та же, что на боевом 5000
        # (боевой цикл на 5050 итераций с O(n²) дедупом крутился бы десятки секунд впустую).
        import store as _store_mod
        orig = _store_mod._SEEN_CAP
        _store_mod._SEEN_CAP = 20
        try:
            s = Store(self.path, cap=100000)
            for i in range(_store_mod._SEEN_CAP + 50):
                self.assertTrue(s.add_idea(_idea(f"уникальнаятема{i}")))
            self.assertLessEqual(len(s.data["seen"]), _store_mod._SEEN_CAP)
        finally:
            _store_mod._SEEN_CAP = orig

    def test_backfill_from_legacy_state(self):
        legacy = {"cap": 3, "tick": 1, "seq": 1, "cursor": 0, "finish": None,
                  "ideas": [{"title": "Старая идея", "why": "w", "effort": "средне",
                             "brain": "stub", "kind": "new", "id": 1, "status": "open", "born_tick": 0}]}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False)
        s = Store(self.path, cap=3)                                  # без поля seen
        self.assertFalse(s.add_idea(_idea("старая идея")))           # засеяно из legacy -> не повторяем


if __name__ == "__main__":
    unittest.main(verbosity=2)
