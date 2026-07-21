"""Тесты для bootstrap_paths.py (ensure_project_paths + ensure_data_dirs)."""

import os
import shutil
import sys
import tempfile
import unittest

import bootstrap_paths


class TestBootstrapPaths(unittest.TestCase):
    """Тесты для path-bootstrap (sys.path)."""

    def test_ensure_project_paths_idempotent(self):
        """Двойной вызов ensure_project_paths не падает и не дублирует пути."""
        # Сохраняем исходную длину sys.path
        original_len = len(sys.path)
        # Первый вызов
        bootstrap_paths.ensure_project_paths()
        after_first_len = len(sys.path)
        # Второй вызов (кэш сработает)
        bootstrap_paths.ensure_project_paths()
        after_second_len = len(sys.path)

        # Длина не изменилась после второго вызова (кэш)
        self.assertEqual(after_first_len, after_second_len)
        # Но после первого вызова путь должен был добавиться (если его не было)
        # Проверим, что cyborg/ и idea_engine/ теперь в sys.path
        here = os.path.dirname(os.path.abspath(__file__))  # .../kiborg/cyborg/tests
        cyborg = os.path.dirname(here)  # .../kiborg/cyborg
        idea = os.path.abspath(os.path.join(cyborg, "..", "idea_engine"))  # .../kiborg/idea_engine
        self.assertIn(cyborg, sys.path)
        self.assertIn(idea, sys.path)


class TestEnsureDataDirs(unittest.TestCase):
    """Тесты для ensure_data_dirs (создание data dirs)."""

    def setUp(self):
        """Создаём временный config для теста (перенаправляем data dirs)."""
        import config

        self.orig_cyb_dir = config.CYBORG_DATA_DIR
        self.orig_ie_dir = config.IDEA_ENGINE_DATA_DIR
        self.orig_backup_dir = config.BACKUPS_DIR

        # Направляем в tempdir (не трогаем реальные data)
        self.tmpdir = tempfile.mkdtemp()
        config.CYBORG_DATA_DIR = os.path.join(self.tmpdir, "cyborg_data")
        config.IDEA_ENGINE_DATA_DIR = os.path.join(self.tmpdir, "ie_data")
        config.BACKUPS_DIR = os.path.join(self.tmpdir, "backups")

        # Сбросить кэш, чтобы тест реально создавал директории
        bootstrap_paths._DIRS_DONE = False

    def tearDown(self):
        """Удаляем временный config и tempdir."""
        import config

        config.CYBORG_DATA_DIR = self.orig_cyb_dir
        config.IDEA_ENGINE_DATA_DIR = self.orig_ie_dir
        config.BACKUPS_DIR = self.orig_backup_dir
        bootstrap_paths._DIRS_DONE = False  # сброс для безопасности

        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def test_ensure_data_dirs_creates_missing_dirs(self):
        """Создаёт отсутствующие директории."""
        import config

        # Директорий нет
        self.assertFalse(os.path.exists(config.CYBORG_DATA_DIR))
        self.assertFalse(os.path.exists(config.IDEA_ENGINE_DATA_DIR))
        self.assertFalse(os.path.exists(config.BACKUPS_DIR))

        # Вызов
        bootstrap_paths.ensure_data_dirs()

        # Теперь есть
        self.assertTrue(os.path.exists(config.CYBORG_DATA_DIR))
        self.assertTrue(os.path.exists(config.IDEA_ENGINE_DATA_DIR))
        self.assertTrue(os.path.exists(config.BACKUPS_DIR))

    def test_ensure_data_dirs_idempotent(self):
        """Повторный вызов не падает и не трогает файлы."""
        import config

        # Первый вызов
        bootstrap_paths.ensure_data_dirs()

        # Создаём файл внутри одной из директорий
        test_file = os.path.join(config.CYBORG_DATA_DIR, "test.txt")
        with open(test_file, "w") as f:
            f.write("hello")

        # Второй вызов
        bootstrap_paths.ensure_data_dirs()

        # Файл не потерялся
        self.assertTrue(os.path.exists(test_file))
        with open(test_file) as f:
            self.assertEqual(f.read(), "hello")

    def test_ensure_data_dirs_respects_cache(self):
        """Кэш _DIRS_DONE блокирует повторные проверки."""
        import config

        # Первый вызов создаёт директории
        bootstrap_paths.ensure_data_dirs()
        self.assertTrue(bootstrap_paths._DIRS_DONE)

        # Удаляем одну директорию вручную (но кэш=True)
        shutil.rmtree(config.CYBORG_DATA_DIR)
        self.assertFalse(os.path.exists(config.CYBORG_DATA_DIR))

        # Второй вызов ничего не делает (кэш)
        bootstrap_paths.ensure_data_dirs()
        self.assertFalse(os.path.exists(config.CYBORG_DATA_DIR))  # не создалась


if __name__ == "__main__":
    # Для запуска через `python -m unittest cyborg/tests/test_bootstrap.py`
    import sys  # noqa: F401  (используется выше в тесте)

    unittest.main()
