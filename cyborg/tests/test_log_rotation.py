"""Тесты ротации runs.md (cyborg/harvest_log._rotate_if_needed).

Фиксируем:
  1. Файл > MAX_LOG_ENTRIES → обрезается до последних N строк.
  2. Файл ≤ MAX_LOG_ENTRIES → не трогается (идемпотентность).
  3. Нет файла → no-op (не падает).
  4. MAX_LOG_ENTRIES читается из config (патчится — независим от дефолта).
Формат runs.md построчный: 1 прогон = 1 строка. Парсер serve._read_runs считает строками.
"""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import harvest_log  # noqa: E402


class TestRotateIfNeeded(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rotate_")
        self.path = os.path.join(self.tmp, "runs.md")
        # MAX_LOG_ENTRIES патчится — тест не зависит от реального дефолта (1000).
        self._orig_max = harvest_log.config.MAX_LOG_ENTRIES
        harvest_log.config.MAX_LOG_ENTRIES = 5

    def tearDown(self):
        harvest_log.config.MAX_LOG_ENTRIES = self._orig_max

    def _write_lines(self, n):
        """Писать n строк вида 'line-0001', 'line-0002', ... в self.path."""
        with open(self.path, "w", encoding="utf-8") as f:
            for i in range(1, n + 1):
                f.write(f"line-{i:04d}\n")

    def _read_lines(self):
        with open(self.path, encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f.readlines()]

    def test_truncates_to_last_n(self):
        # 12 строк, лимит 5 → остаётся 5 последних (line-0008 ... line-0012)
        self._write_lines(12)
        harvest_log._rotate_if_needed(self.path)
        lines = self._read_lines()
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], "line-0008")  # первая оставленная = 8-я (12-5+1)
        self.assertEqual(lines[-1], "line-0012")  # последняя сохранилась

    def test_no_truncate_under_limit(self):
        # 3 строки, лимит 5 → файл НЕ ТРОГАТЬ (идемпотентность)
        self._write_lines(3)
        original = self._read_lines()
        harvest_log._rotate_if_needed(self.path)
        self.assertEqual(self._read_lines(), original)

    def test_exact_limit_no_truncate(self):
        # Ровно лимит (5 == 5) → не трогать
        self._write_lines(5)
        original = self._read_lines()
        harvest_log._rotate_if_needed(self.path)
        self.assertEqual(self._read_lines(), original)

    def test_missing_file_noop(self):
        # Нет файла → no-op (не падает). Сценарий: первый прогон / удалён вручную.
        missing = os.path.join(self.tmp, "no_such.md")
        harvest_log._rotate_if_needed(missing)  # не должна выбросить

    def test_atomic_no_tmp_left(self):
        # После ротации .tmp-файла рядом быть не должно (atomic_write через os.replace)
        self._write_lines(12)
        harvest_log._rotate_if_needed(self.path)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_respects_patched_max(self):
        # Поставили лимит 3 (не дефолт 1000) → обрезает до 3
        harvest_log.config.MAX_LOG_ENTRIES = 3
        self._write_lines(10)
        harvest_log._rotate_if_needed(self.path)
        self.assertEqual(len(self._read_lines()), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
