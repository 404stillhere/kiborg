"""Тесты руля направления (cyborg/direction.py): дефолты, сохранение, чистка пресетов, атомарность."""

import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import direction  # noqa: E402


class TestDirection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dir_")
        self._orig = direction.PATH
        direction.PATH = os.path.join(self.tmp, "direction.json")

    def tearDown(self):
        direction.PATH = self._orig

    def test_default_when_no_file(self):
        d = direction.load()
        self.assertEqual(d["current"], "")  # пусто = без направления
        self.assertEqual(d["presets"], direction._DEFAULT_PRESETS)
        self.assertEqual(direction.current(), "")

    def test_save_and_read_current(self):
        direction.save(current="железки")
        self.assertEqual(direction.current(), "железки")
        # пресеты не трогали при сохранении только current — остались дефолтные
        self.assertEqual(direction.load()["presets"], direction._DEFAULT_PRESETS)

    def test_save_persists_to_disk_atomic(self):
        direction.save(current="игры", presets=["игры", "здоровье"])
        with open(direction.PATH, encoding="utf-8") as f:
            json.load(f)  # валидный JSON на диске
        self.assertFalse(os.path.exists(direction.PATH + ".tmp"))
        self.assertEqual(direction.load()["current"], "игры")
        self.assertEqual(direction.load()["presets"], ["игры", "здоровье"])

    def test_presets_cleaned_dedup_and_empty(self):
        direction.save(presets=["  дев  ", "дев", "ДЕВ", "", "   ", "игры"])
        # тримминг + дедуп регистронезависимо + выкинуть пустые
        self.assertEqual(direction.load()["presets"], ["дев", "игры"])

    def test_current_trimmed_and_capped(self):
        direction.save(current="  " + "x" * 300 + "  ")
        cur = direction.current()
        self.assertEqual(len(cur), direction._MAX_LEN)  # обрезано по потолку
        self.assertFalse(cur.startswith(" "))  # затримлено

    def test_presets_capped(self):
        direction.save(presets=[f"тема{i}" for i in range(direction._MAX_PRESETS + 20)])
        self.assertEqual(len(direction.load()["presets"]), direction._MAX_PRESETS)

    def test_clear_current_back_to_none(self):
        direction.save(current="бизнес")
        self.assertEqual(direction.current(), "бизнес")
        direction.save(current="")  # снять руль
        self.assertEqual(direction.current(), "")

    def test_broken_file_falls_to_default(self):
        with open(direction.PATH, "w", encoding="utf-8") as f:
            f.write("{битый json")
        self.assertEqual(direction.current(), "")  # не падаем, дефолт
        self.assertEqual(direction.load()["presets"], direction._DEFAULT_PRESETS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
