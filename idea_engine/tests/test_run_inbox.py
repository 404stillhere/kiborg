"""Тесты инбокс-рендера idea_engine/run.py: Дорожка C — регенерация Oracle-планов
из oracles/index.md (council 2026-08-17, #2: deliver_oracle дописывает карточку "a",
_write_inbox переписывает файл "w" — без регенерации планы стирались каждым тиком)."""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import run as ie_run  # noqa: E402  (idea_engine/run.py; свой процесс — коллизии имён нет)


class _FakeStore:
    """Минимальный store для _write_inbox: без органов и персиста."""

    def __init__(self, ideas=None):
        self.data = {"tick": 7, "cap": 0, "finish": None, "ideas": ideas or []}

    def open_ideas(self):
        return self.data["ideas"]

    def cleared_count(self):
        return 0


class TestOracleSection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="inbox_oracle_")
        self._saved = (ie_run.ORACLE_INDEX, ie_run.INBOX)
        ie_run.INBOX = os.path.join(self.tmp, "inbox.md")
        self.index = os.path.join(self.tmp, "index.md")
        ie_run.ORACLE_INDEX = self.index

    def tearDown(self):
        ie_run.ORACLE_INDEX, ie_run.INBOX = self._saved

    def _write_index(self, text):
        with open(self.index, "w", encoding="utf-8") as f:
            f.write(text)

    def test_no_index_no_section(self):
        ie_run._write_inbox(_FakeStore())
        body = open(ie_run.INBOX, encoding="utf-8").read()
        self.assertNotIn("Дорожка C", body)  # без планов формат инбокса как раньше

    def test_section_regenerated_from_index(self):
        self._write_index(
            "# Индекс планов Oracle\n"
            "\n"
            "- [Auth plan](note-bot/2026-08-04_13-52-06.md) — add Telegram auth (1 шагов, 2026-08-04 14:45)\n"
            "- [Пагинация](kiborg/2026-08-04_12-55-51.md) — добавить пагинацию (3 шагов, 2026-08-04 12:55)\n"
        )
        ie_run._write_inbox(_FakeStore())
        body = open(ie_run.INBOX, encoding="utf-8").read()
        self.assertIn("## Дорожка C — планы Oracle", body)
        self.assertIn("**Auth plan** — `add Telegram auth`", body)
        self.assertIn("проект: `note-bot`", body)
        self.assertIn("план: `oracles/note-bot/2026-08-04_13-52-06.md`", body)
        self.assertIn("**Пагинация**", body)

    def test_lost_plans_come_back(self):
        # суть бага #2: карточка Оракула, дописанная в конец inbox.md, стирается тиком;
        # регенерация из индекса ВОЗВРАЩАЕТ план в инбокс без участия органа
        self._write_index(
            "- [План из прошлого](proj/2026-08-15_10-00-00.md) — цель плана (2 шагов, 2026-08-15 10:00)\n"
        )
        with open(ie_run.INBOX, "w", encoding="utf-8") as f:
            f.write("# Инбокс идей киборга\n...старое тело без планов...\n")
        ie_run._write_inbox(_FakeStore())
        body = open(ie_run.INBOX, encoding="utf-8").read()
        self.assertIn("План из прошлого", body)  # план вернулся из индекса

    def test_duplicate_index_entries_shown_once(self):
        # известная шероховатость индекса: при замене дубля-плана старая строка остаётся
        self._write_index(
            "- [Дубль](demo/2026-08-15_15-35-13.md) — цель (1 шагов, 2026-08-15 15:35)\n"
            "- [Дубль](demo/2026-08-15_15-35-13.md) — цель (1 шагов, 2026-08-15 15:35)\n"
        )
        sec = "\n".join(ie_run._oracle_section())
        self.assertEqual(sec.count("demo/2026-08-15_15-35-13.md"), 1)

    def test_index_with_bom_is_readable(self):
        with open(self.index, "w", encoding="utf-8-sig") as f:
            f.write("- [BOM план](p/1.md) — цель (1 шагов, 2026-08-16 09:00)\n")
        sec = "\n".join(ie_run._oracle_section())
        self.assertIn("BOM план", sec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
