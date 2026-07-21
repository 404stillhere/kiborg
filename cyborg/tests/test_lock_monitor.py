"""Тесты счётчика таймаутов state_lock.

lock_monitor.record_timeout() / recent_timeouts(minutes) — лёгкий in-memory счётчик
для /api/health. Хранение list[float] под threading.Lock. Cleanup устаревших — lazy
при вызове recent_timeouts.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lock_monitor  # noqa: E402


class TestRecordAndRecent(unittest.TestCase):
    def setUp(self):
        lock_monitor.reset()

    def tearDown(self):
        lock_monitor.reset()

    def test_zero_timeouts_returns_zero(self):
        self.assertEqual(lock_monitor.recent_timeouts(60), 0)

    def test_single_timeout_counted(self):
        lock_monitor.record_timeout()
        self.assertEqual(lock_monitor.recent_timeouts(60), 1)

    def test_multiple_timeouts_counted(self):
        for _ in range(5):
            lock_monitor.record_timeout()
        self.assertEqual(lock_monitor.recent_timeouts(60), 5)

    def test_default_window_is_60_minutes(self):
        # recent_timeouts() без аргумента — дефолт 60 мин (как вызывает serve._health)
        lock_monitor.record_timeout()
        self.assertEqual(lock_monitor.recent_timeouts(), 1)


class TestTimeWindow(unittest.TestCase):
    """Записи старше окна не считаются и заодно очищаются из списка."""

    def setUp(self):
        lock_monitor.reset()

    def tearDown(self):
        lock_monitor.reset()

    def test_old_record_excluded_from_count(self):
        # Вручную кладём «старую» метку (2 часа назад) + одну свежую.
        old_ts = time.time() - 120 * 60  # 2 часа назад — за пределами 60-мин окна
        lock_monitor._TIMECTS.append(old_ts)
        lock_monitor.record_timeout()
        # Окно 60 мин: должна учитываться только свежая.
        self.assertEqual(lock_monitor.recent_timeouts(60), 1)

    def test_just_outside_window_excluded(self):
        # метка 61 мин назад — за пределами 60-мин окна
        lock_monitor._TIMECTS.append(time.time() - 61 * 60)
        lock_monitor.record_timeout()
        self.assertEqual(lock_monitor.recent_timeouts(60), 1)  # только свежая

    def test_just_inside_window_included(self):
        # метка 59 мин назад — ещё внутри 60-мин окна
        lock_monitor._TIMECTS.append(time.time() - 59 * 60)
        lock_monitor.record_timeout()
        self.assertEqual(lock_monitor.recent_timeouts(60), 2)  # обе

    def test_custom_small_window(self):
        # окно 1 мин: свежие записи (только что) все в окне
        for _ in range(3):
            lock_monitor.record_timeout()
        self.assertEqual(lock_monitor.recent_timeouts(1), 3)

    def test_window_zero_excludes_everything_older_than_now(self):
        # окно 0 мин = ничего не должно учитываться (всё строго старше cutoff).
        # Но: race — запись может попасть в ту же миллисекунду; проверяем что результат
        # точно не больше 1 (по сути 0 для практически всех записей).
        lock_monitor.record_timeout()
        result = lock_monitor.recent_timeouts(0)
        self.assertIn(result, (0, 1))  # толерантность к millisecond-race


class TestCleanupBehavior(unittest.TestCase):
    """recent_timecuts() чистит список — он не растёт бесконечно при долгоживущем процессе."""

    def setUp(self):
        lock_monitor.reset()

    def tearDown(self):
        lock_monitor.reset()

    def test_old_records_removed_from_storage(self):
        # 3 старых + 2 свежих. После recent_timeouts(60) в списке должно остаться только 2.
        for _ in range(3):
            lock_monitor._TIMECTS.append(time.time() - 120 * 60)
        for _ in range(2):
            lock_monitor.record_timeout()
        self.assertEqual(lock_monitor.recent_timeouts(60), 2)
        self.assertEqual(len(lock_monitor._TIMECTS), 2)  # cleanup произошёл

    def test_all_old_records_cleanup_leaves_empty(self):
        # все 5 записей — старые. После recent_timeouts список пуст.
        for _ in range(5):
            lock_monitor._TIMECTS.append(time.time() - 200 * 60)
        self.assertEqual(lock_monitor.recent_timeouts(60), 0)
        self.assertEqual(len(lock_monitor._TIMECTS), 0)

    def test_cleanup_idempotent_when_called_repeatedly(self):
        lock_monitor.record_timeout()
        lock_monitor.recent_timeouts(60)
        first_len = len(lock_monitor._TIMECTS)
        lock_monitor.recent_timeouts(60)
        second_len = len(lock_monitor._TIMECTS)
        self.assertEqual(first_len, second_len)  # повторный вызов не дублирует ничего

    def test_subsequent_calls_with_different_windows(self):
        # окно 120 мин учитывает запись час назад, окно 30 мин — уже нет.
        old_ts = time.time() - 50 * 60  # 50 мин назад
        lock_monitor._TIMECTS.append(old_ts)
        # ВНИМАНИЕ: первый вызов с окном 120 НЕ вычистит 50-мин запись (она в окне),
        # но последующий с окном 30 — вычистит.
        self.assertEqual(lock_monitor.recent_timeouts(120), 1)  # 50 мин < 120 мин
        self.assertEqual(len(lock_monitor._TIMECTS), 1)
        self.assertEqual(lock_monitor.recent_timeouts(30), 0)  # 50 мин > 30 мин
        self.assertEqual(len(lock_monitor._TIMECTS), 0)  # вычищена


class TestThreadSafety(unittest.TestCase):
    """Параллельная запись из многих потоков не теряет записи и не роняет процесс."""

    def setUp(self):
        lock_monitor.reset()

    def tearDown(self):
        lock_monitor.reset()

    def test_concurrent_records_all_counted(self):
        N_THREADS = 10
        N_PER_THREAD = 100
        threads = []

        def hammer():
            for _ in range(N_PER_THREAD):
                lock_monitor.record_timeout()

        for _ in range(N_THREADS):
            threads.append(threading.Thread(target=hammer))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Ни одна запись не потеряна под гонкой.
        self.assertEqual(lock_monitor.recent_timeouts(60), N_THREADS * N_PER_THREAD)

    def test_concurrent_read_and_write(self):
        # Пока одни потоки пишут, другой читает — не должно падать/виснуть.
        stop = threading.Event()
        errors = []

        def writer():
            while not stop.is_set():
                lock_monitor.record_timeout()

        def reader():
            try:
                for _ in range(50):
                    lock_monitor.recent_timeouts(60)
            except Exception as e:
                errors.append(e)

        wt = [threading.Thread(target=writer) for _ in range(3)]
        rt = threading.Thread(target=reader)
        for t in wt:
            t.start()
        rt.start()
        rt.join()
        stop.set()
        for t in wt:
            t.join()
        self.assertEqual(errors, [])  # читатель не упал


class TestReset(unittest.TestCase):
    def test_reset_clears_all(self):
        for _ in range(10):
            lock_monitor.record_timeout()
        self.assertEqual(lock_monitor.recent_timeouts(60), 10)
        lock_monitor.reset()
        self.assertEqual(lock_monitor.recent_timeouts(60), 0)
        self.assertEqual(len(lock_monitor._TIMECTS), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
