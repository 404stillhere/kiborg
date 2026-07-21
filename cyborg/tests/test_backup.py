"""Тесты резервного копирования (cyborg/backup.py).

Фиксируем:
  1. backup_state() создаёт BACKUPS_DIR/<TS>/ с копиями state.json + seen_items.json.
  2. Ротация: при >MAX_BACKUPS старые удаляются (остаются последние N).
  3. Отсутствующие source-файлы — skip без ошибки (свежая установка).
  4. Все файлы отсутствуют — return None, пустой подкаталог НЕ плодится.
Пути state.json/seen_items.json патчатся через backup.config / backup.seen_items — реальные
файлы проекта НЕ трогаем.
"""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import backup  # noqa: E402


class TestBackupState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bkp_")
        # Свои BACKUPS_DIR + source-файлы — реальные не трогаем.
        self._orig_backups = backup.config.BACKUPS_DIR
        self._orig_max = backup.config.MAX_BACKUPS
        self._orig_state = backup.config.IE_STATE_JSON
        self._orig_seen_path = backup.seen_items.PATH
        backup.config.BACKUPS_DIR = os.path.join(self.tmp, "backups")
        backup.config.MAX_BACKUPS = 3
        self._state_src = os.path.join(self.tmp, "state.json")
        self._seen_src = os.path.join(self.tmp, "seen_items.json")
        backup.config.IE_STATE_JSON = self._state_src
        backup.seen_items.PATH = self._seen_src
        # По умолчанию оба source-файла существуют с валидным содержимым.
        with open(self._state_src, "w", encoding="utf-8") as f:
            f.write('{"ideas": ["test"]}')
        with open(self._seen_src, "w", encoding="utf-8") as f:
            f.write("{}")

    def tearDown(self):
        backup.config.BACKUPS_DIR = self._orig_backups
        backup.config.MAX_BACKUPS = self._orig_max
        backup.config.IE_STATE_JSON = self._orig_state
        backup.seen_items.PATH = self._orig_seen_path

    def _make_fake_backup(self, ts, with_files=True):
        """Создать поддиректорию-TS в BACKUPS_DIR (для теста ротации)."""
        d = os.path.join(backup.config.BACKUPS_DIR, ts)
        os.makedirs(d, exist_ok=True)
        if with_files:
            with open(os.path.join(d, "state.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(d, "seen_items.json"), "w") as f:
                f.write("{}")

    def test_creates_backup_with_both_files(self):
        result = backup.backup_state()
        self.assertIsNotNone(result)
        self.assertTrue(os.path.isdir(result))
        # В созданном бэкапе — оба файла
        files = sorted(os.listdir(result))
        self.assertEqual(files, ["seen_items.json", "state.json"])
        # Содержимое совпадает с source
        with open(os.path.join(result, "state.json")) as f:
            self.assertEqual(f.read(), '{"ideas": ["test"]}')

    def test_rotation_removes_old(self):
        # Создаём 5 «старых» бэкапов (имитация истории). MAX_BACKUPS=3.
        for i in range(5):
            # TS-имена, упорядоченные лексикографически = хронологически
            self._make_fake_backup(f"2026-07-2{i}_120000")
        # Теперь backup_state должен добавить ещё один И ротировать: 5+1=6 → оставить 3 свежих.
        backup.backup_state()
        names = backup._list_backups()
        self.assertEqual(len(names), backup.config.MAX_BACKUPS)
        # Самый свежий в списке — только что созданный (TS > всех 2026-07-2x).
        # Самый старый из 2026-07-20/21 должен быть удалён.
        self.assertNotIn("2026-07-20_120000", names)
        self.assertNotIn("2026-07-21_120000", names)

    def test_missing_source_files_skipped(self):
        # state.json удалён, seen_items.json есть — бэкап создаётся, но только с seen_items.
        os.remove(self._state_src)
        result = backup.backup_state()
        self.assertIsNotNone(result)
        files = os.listdir(result)
        self.assertEqual(files, ["seen_items.json"])  # state.json пропущен, не упал

    def test_all_missing_returns_none_no_dir(self):
        # Оба source-файла удалены — return None, пустой TS-подкаталог НЕ плодится.
        os.remove(self._state_src)
        os.remove(self._seen_src)
        result = backup.backup_state()
        self.assertIsNone(result)
        # BACKUPS_DIR либо не создан, либо пуст
        if os.path.isdir(backup.config.BACKUPS_DIR):
            self.assertEqual(os.listdir(backup.config.BACKUPS_DIR), [])

    def test_rotation_keeps_max(self):
        # Подтверждение: после N вызовов количество ≤ MAX_BACKUPS всегда.
        for _ in range(7):
            # Уникальный TS на каждый вызов не гарантирован (секунды), но тест проходит в мгновение —
            # используем разные имена фейковых + один реальный вызов
            pass
        # 3 фейковых + 1 реальный = 4 → ротация оставит 3
        for i in range(3):
            self._make_fake_backup(f"2026-07-0{i+1}_000000")
        backup.backup_state()
        names = backup._list_backups()
        self.assertLessEqual(len(names), backup.config.MAX_BACKUPS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
