"""Тесты автоматического восстановления state.json (cyborg/recover_state.py).

Покрываем все ветки auto_recover_state_if_needed():
  1. Валидный state.json → no-op.
  2. Повреждённый JSON → успешное восстановление из свежего бэкапа.
  3. Отсутствующий state.json → успешное восстановление.
  4. Нет бэкапов → ошибка, текущий (битый) файл НЕ тронут.
  5. Бэкап тоже повреждён → ошибка.
  6. Повреждённый файл сохраняется как .corrupted-<TS> перед перезаписью.

И проверка выбора САМОГО СВЕЖЕГО бэкапа при нескольких кандидатах.
"""

import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import recover_state  # noqa: E402


class _Base(unittest.TestCase):
    """Общий setup: tmp-каталог с state.json + backups/ (изолированно от проекта)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rec_test_")
        self.state = os.path.join(self.tmp, "state.json")
        self.backups = os.path.join(self.tmp, "backups")
        os.makedirs(self.backups)

    def _make_backup(self, ts, payload):
        """Создать поддиректорию backups/<ts>/state.json с данным содержимым."""
        bdir = os.path.join(self.backups, ts)
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "state.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return bdir

    def _write_state(self, content):
        with open(self.state, "w", encoding="utf-8") as f:
            f.write(content)


class TestNoOpWhenValid(_Base):
    def test_valid_state_is_noop(self):
        # state.json валиден → recovered=False, error=None, файл НЕ тронут
        self._write_state('{"ideas": ["старая идея"], "tick": 42}')
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertFalse(result["recovered"])
        self.assertIsNone(result["backup_ts"])
        self.assertIsNone(result["error"])
        # файл на месте, содержимое не изменилось
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["tick"], 42)


class TestRecoverFromCorrupted(_Base):
    def test_corrupted_json_restores_from_backup(self):
        # state.json — битый JSON; в бэкапе есть валидный → восстанавливаем
        self._write_state("{ это не json !!!")
        self._make_backup("2026-07-21_100000", {"ideas": ["из бэкапа"], "tick": 10})
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertTrue(result["recovered"])
        self.assertEqual(result["backup_ts"], "2026-07-21_100000")
        self.assertIsNone(result["error"])
        # state.json теперь содержит бэкап
        with open(self.state, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["ideas"], ["из бэкапа"])

    def test_corrupted_saves_corrupted_copy(self):
        # при восстановлении битый файл сохраняется как .corrupted-<TS> для разбора
        self._write_state("{ битый")
        self._make_backup("2026-07-21_100000", {"ideas": []})
        recover_state.auto_recover_state_if_needed(self.state, self.backups)
        corrupted_copies = [f for f in os.listdir(self.tmp) if f.startswith("state.json.corrupted-")]
        self.assertEqual(len(corrupted_copies), 1)
        # содержимое дампа = оригинальный битый текст (для разбора)
        with open(os.path.join(self.tmp, corrupted_copies[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ битый")


class TestRecoverFromMissing(_Base):
    def test_missing_state_restores_from_backup(self):
        # файла нет вообще (удалили / первый запуск после краха) — восстанавливаем из бэкапа
        # (state_path НЕ существует на старте)
        self.assertFalse(os.path.exists(self.state))
        self._make_backup("2026-07-21_090000", {"ideas": ["восстановлено"]})
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertTrue(result["recovered"])
        self.assertEqual(result["backup_ts"], "2026-07-21_090000")
        # файл создан из бэкапа
        self.assertTrue(os.path.exists(self.state))
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["ideas"], ["восстановлено"])

    def test_missing_state_no_corrupted_copy_created(self):
        # при отсутствии исходного файла дамп НЕ создаётся (сохранять нечего)
        self._make_backup("2026-07-21_090000", {"ideas": []})
        recover_state.auto_recover_state_if_needed(self.state, self.backups)
        corrupted_copies = [f for f in os.listdir(self.tmp) if f.startswith("state.json.corrupted-")]
        self.assertEqual(corrupted_copies, [])


class TestNoBackupAvailable(_Base):
    def test_corrupted_no_backup_returns_error(self):
        # state.json битый, бэкапов нет → error, текущий файл НЕ тронут (не затираем потерянное)
        self._write_state("{ битый")
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertFalse(result["recovered"])
        self.assertIsNotNone(result["error"])
        self.assertIn("no valid backup", result["error"])
        # оригинал на месте, не перезаписан
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ битый")

    def test_corrupted_backup_also_corrupted_returns_error(self):
        # state.json битый И единственный бэкап тоже битый → error, оригинал не тронут
        self._write_state("{ битый текущий")
        # создаём подкаталог бэкапа, но state.json внутри — тоже мусор
        bdir = os.path.join(self.backups, "2026-07-21_100000")
        os.makedirs(bdir)
        with open(os.path.join(bdir, "state.json"), "w", encoding="utf-8") as f:
            f.write("{ тоже битый")
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertFalse(result["recovered"])
        self.assertIsNotNone(result["error"])
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ битый текущий")

    def test_fresh_install_no_state_no_backup(self):
        # нет state.json и нет бэкапов — нормальная стартовая точка (fresh install).
        # Возвращаем ошибку с пояснением, файл НЕ создаём (прогон упадёт в Organs с прозрачной ошибкой,
        # это лучше тихой пустой инициализации).
        self.assertFalse(os.path.exists(self.state))
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertFalse(result["recovered"])
        self.assertIsNotNone(result["error"])
        self.assertIn("fresh install", result["error"])
        self.assertFalse(os.path.exists(self.state))  # пустоту не плодим


class TestSelectsLatestBackup(_Base):
    def test_picks_most_recent_valid_backup(self):
        # несколько бэкапов, выбираем САМЫЙ СВЕЖИЙ по TS-имени (лексикографическая = хронологическая)
        self._write_state("{ битый")
        self._make_backup("2026-07-20_080000", {"ideas": ["старый бэкап"]})
        self._make_backup("2026-07-21_100000", {"ideas": ["новый бэкап"]})
        self._make_backup("2026-07-20_200000", {"ideas": ["средний бэкап"]})
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertTrue(result["recovered"])
        self.assertEqual(result["backup_ts"], "2026-07-21_100000")  # самый свежий
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["ideas"], ["новый бэкап"])

    def test_skips_corrupted_backups_to_older_valid(self):
        # свежий бэкап битый — берём следующий свежий валидный, не сдаёмся сразу
        self._write_state("{ битый")
        # создаем «свежий» битый бэкап
        bdir_new = os.path.join(self.backups, "2026-07-21_100000")
        os.makedirs(bdir_new)
        open(os.path.join(bdir_new, "state.json"), "w").write("{ битый бэкап")
        # и старший валидный
        self._make_backup("2026-07-20_080000", {"ideas": ["старый но рабочий"]})
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertTrue(result["recovered"])
        self.assertEqual(result["backup_ts"], "2026-07-20_080000")  # перепрыгнули битый


class TestEdgeCases(_Base):
    def test_empty_backups_dir(self):
        # backups/ существует, но пустой → no valid backup
        self._write_state("{ битый")
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertFalse(result["recovered"])
        self.assertIsNotNone(result["error"])

    def test_missing_backups_dir(self):
        # каталог backups/ не существует (ensure_data_dirs не запущен) — не падаем
        self._write_state("{ битый")
        os.rmdir(self.backups)
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertFalse(result["recovered"])
        self.assertIsNotNone(result["error"])

    def test_state_empty_file_treated_as_corrupted(self):
        # пустой файл (0 байт) — это битый JSON → восстанавливаем, если есть бэкап
        self._write_state("")
        self._make_backup("2026-07-21_100000", {"ideas": ["из бэкапа"]})
        result = recover_state.auto_recover_state_if_needed(self.state, self.backups)
        self.assertTrue(result["recovered"])
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["ideas"], ["из бэкапа"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
