"""Тесты списка папок-источника (cyborg/folders.py): дефолт-пусто, сохранение, чистка, атомарность."""
import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import folders  # noqa: E402


class TestFolders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fold_")
        self._orig = folders.PATH
        folders.PATH = os.path.join(self.tmp, "folders.json")

    def tearDown(self):
        folders.PATH = self._orig

    def test_default_when_no_file(self):
        self.assertEqual(folders.load(), {"folders": [], "paths": []})  # нет файла = источник выключен
        self.assertEqual(folders.current(), [])

    def test_save_and_read(self):
        folders.save(["M:/projects/kiborg"])
        self.assertEqual(folders.current(), ["M:/projects/kiborg"])

    def test_save_persists_to_disk_atomic(self):
        folders.save(["M:/a", "C:/b"])
        with open(folders.PATH, encoding="utf-8") as f:
            json.load(f)                                    # валидный JSON на диске
        self.assertFalse(os.path.exists(folders.PATH + ".tmp"))
        self.assertEqual(folders.load()["paths"], ["M:/a", "C:/b"])

    def test_cleaned_dedup_trim_and_empty(self):
        folders.save(["  M:/x  ", "M:/x", "m:/X", "", "   ", "C:/y"])
        # тримминг + дедуп регистронезависимо + выкинуть пустые (порядок первого вхождения)
        self.assertEqual(folders.load()["paths"], ["M:/x", "C:/y"])

    def test_backslashes_and_trailing_slash_normalized(self):
        folders.save(["M:\\projects\\kiborg\\", 'C:/Users/User/notes/'])
        self.assertEqual(folders.load()["paths"], ["M:/projects/kiborg", "C:/Users/User/notes"])

    def test_drive_root_preserved(self):
        folders.save(["M:/", "C:\\"])
        self.assertEqual(folders.load()["paths"], ["M:/", "C:/"])   # корень диска не схлопнут в «M:»

    def test_capped(self):
        folders.save([f"M:/p{i}" for i in range(folders._MAX_PATHS + 15)])
        self.assertEqual(len(folders.load()["paths"]), folders._MAX_PATHS)

    def test_len_capped(self):
        folders.save(["M:/" + "x" * 800])
        self.assertLessEqual(len(folders.current()[0]), folders._MAX_LEN)

    def test_non_list_ignored(self):
        folders.save("не список")                            # не список -> пусто, не падаем
        self.assertEqual(folders.current(), [])

    def test_broken_file_falls_to_empty(self):
        with open(folders.PATH, "w", encoding="utf-8") as f:
            f.write("{битый json")
        self.assertEqual(folders.current(), [])              # не падаем, источник выключен

    def test_non_str_entries_skipped(self):
        folders.save(["M:/ok", 123, None, "C:/ok2"])
        self.assertEqual(folders.current(), ["M:/ok", "C:/ok2"])

    # --- индивидуальный вкл/выкл (2026-07-18) ---

    def test_string_items_default_on(self):
        folders.save(["M:/a", "M:/b"])                       # строки = включены по умолчанию
        self.assertEqual(folders.load()["folders"],
                         [{"path": "M:/a", "on": True}, {"path": "M:/b", "on": True}])

    def test_off_folder_excluded_from_current(self):
        folders.save([{"path": "M:/on", "on": True}, {"path": "M:/off", "on": False}])
        self.assertEqual(folders.current(), ["M:/on"])       # прогон видит только включённые
        self.assertEqual(folders.all_paths(), ["M:/on", "M:/off"])  # проба пульта — все

    def test_all_off_disables_source(self):
        folders.save([{"path": "M:/x", "on": False}])
        self.assertEqual(folders.current(), [])              # все выкл = источник-папка выключен
        self.assertEqual(len(folders.load()["folders"]), 1)  # но папка в списке остаётся

    def test_toggle_preserves_order_and_flags(self):
        folders.save([{"path": "M:/a", "on": True},
                      {"path": "M:/b", "on": False},
                      {"path": "M:/c", "on": True}])
        self.assertEqual(folders.load()["folders"],
                         [{"path": "M:/a", "on": True},
                          {"path": "M:/b", "on": False},
                          {"path": "M:/c", "on": True}])

    def test_legacy_paths_format_reads_as_all_on(self):
        with open(folders.PATH, "w", encoding="utf-8") as f:
            json.dump({"paths": ["M:/old1", "M:/old2"]}, f)   # старый формат до тумблеров
        self.assertEqual(folders.current(), ["M:/old1", "M:/old2"])
        self.assertEqual(folders.load()["folders"],
                         [{"path": "M:/old1", "on": True}, {"path": "M:/old2", "on": True}])

    def test_dedup_keeps_first_flag(self):
        folders.save([{"path": "M:/dup", "on": False}, {"path": "M:/DUP", "on": True}])
        self.assertEqual(folders.load()["folders"], [{"path": "M:/dup", "on": False}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
