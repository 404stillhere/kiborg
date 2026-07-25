"""Регрессии интерактивного восстановления бэкапа.

Все пути подменяются на временные: тест не читает и не перезаписывает живые state/seen.
"""

import builtins
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import restore_backup  # noqa: E402


class TestInteractiveRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="restore_interactive_")
        self._saved = (
            restore_backup.config.BACKUPS_DIR,
            restore_backup.config.IE_STATE_JSON,
            restore_backup.seen_items.PATH,
        )
        restore_backup.config.BACKUPS_DIR = os.path.join(self.tmp, "backups")
        restore_backup.config.IE_STATE_JSON = os.path.join(self.tmp, "state.json")
        restore_backup.seen_items.PATH = os.path.join(self.tmp, "seen_items.json")
        os.makedirs(restore_backup.config.BACKUPS_DIR)
        with open(restore_backup.config.IE_STATE_JSON, "w", encoding="utf-8") as f:
            f.write('{"ideas": ["current"]}')
        with open(restore_backup.seen_items.PATH, "w", encoding="utf-8") as f:
            f.write('{"current": 1}')
        backup = os.path.join(restore_backup.config.BACKUPS_DIR, "2026-07-25_010000")
        os.makedirs(backup)
        with open(os.path.join(backup, "state.json"), "w", encoding="utf-8") as f:
            f.write('{"ideas": ["backup"]}')
        with open(os.path.join(backup, "seen_items.json"), "w", encoding="utf-8") as f:
            f.write('{"backup": 1}')

    def tearDown(self):
        (
            restore_backup.config.BACKUPS_DIR,
            restore_backup.config.IE_STATE_JSON,
            restore_backup.seen_items.PATH,
        ) = self._saved

    def test_eof_at_confirmation_cancels_without_overwrite(self):
        """Закрытый stdin на втором вопросе — обычная отмена, не traceback и не restore."""
        out = io.StringIO()
        with (
            mock.patch.object(builtins, "input", side_effect=["1", EOFError]),
            contextlib.redirect_stdout(out),
        ):
            restore_backup._interactive()

        self.assertIn("отмена", out.getvalue())
        with open(restore_backup.config.IE_STATE_JSON, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"ideas": ["current"]}')
        self.assertFalse(
            any(".pre-restore-" in name for name in os.listdir(self.tmp)),
            "страховка появляется только перед реальным восстановлением",
        )

    def test_selected_backup_restores_both_files_with_pre_restore_copy(self):
        out = io.StringIO()
        with (
            mock.patch.object(builtins, "input", side_effect=["1", "y"]),
            contextlib.redirect_stdout(out),
        ):
            restore_backup._interactive()

        with open(restore_backup.config.IE_STATE_JSON, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"ideas": ["backup"]}')
        with open(restore_backup.seen_items.PATH, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"backup": 1}')
        pre_restore = [name for name in os.listdir(self.tmp) if ".pre-restore-" in name]
        self.assertEqual(len(pre_restore), 2)
        self.assertIn("восстановлено из 2026-07-25_010000", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
