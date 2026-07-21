"""Тесты опционального алертинга (cyborg/alerts.py).

Фиксируем:
  1. Без ENV (KIBORG_ALERT_TOKEN/CHAT_ID) — maybe_alert печатает в stdout, не падает.
  2. С ENV — POST на api.telegram.org через urllib.request (мокаем urlopen).
  3. Сетевой сбой/таймаут при отправке — тихо падает на print, исключение НЕ прокидывается
     (алертинг не должен ронять прогон киборга).
Мокаем через monkeypatch модуля alerts (urllib.request.urlopen, os.environ) — реальную сеть
в тестах не трогаем. Только stdlib (unittest + io для перехвата stdout).
"""

import io
import os
import sys
import unittest
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import alerts  # noqa: E402


class TestMaybeAlert(unittest.TestCase):
    def setUp(self):
        # Чистим ENV — каждый тест сам решает, задавать ли токен.
        self._orig_token = os.environ.pop("KIBORG_ALERT_TOKEN", None)
        self._orig_chat = os.environ.pop("KIBORG_ALERT_CHAT_ID", None)
        # Перехват stdout — чтобы проверить fallback-логирование.
        self._orig_stdout = sys.stdout

    def tearDown(self):
        if self._orig_token is not None:
            os.environ["KIBORG_ALERT_TOKEN"] = self._orig_token
        if self._orig_chat is not None:
            os.environ["KIBORG_ALERT_CHAT_ID"] = self._orig_chat
        sys.stdout = self._orig_stdout

    def _capture_stdout(self):
        buf = io.StringIO()
        sys.stdout = buf
        return buf

    def test_no_env_logs_to_stdout(self):
        # НЕТ конфигурации TG — падает на print с пометкой [ALERT], исключений нет.
        buf = self._capture_stdout()
        alerts.maybe_alert("WARN", "тестовое сообщение без токена")
        out = buf.getvalue()
        self.assertIn("[ALERT][WARN]", out)
        self.assertIn("тестовое сообщение без токена", out)

    def test_with_env_sends_tg_request(self):
        # ENV задан — urlopen зовётся с URL, содержащим токен и chat_id.
        os.environ["KIBORG_ALERT_TOKEN"] = "123:FAKETOKEN"
        os.environ["KIBORG_ALERT_CHAT_ID"] = "987654321"
        captured = {}

        class _FakeResp:
            def read(self):
                return b'{"ok":true}'

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["data"] = req.data.decode("utf-8")
            captured["method"] = req.method
            captured["timeout"] = timeout
            return _FakeResp()

        orig_urlopen = alerts.urllib.request.urlopen
        alerts.urllib.request.urlopen = fake_urlopen
        try:
            alerts.maybe_alert("CRITICAL", "мозг недоступен")
        finally:
            alerts.urllib.request.urlopen = orig_urlopen
        # URL содержит токен бота и метод sendMessage
        self.assertIn("api.telegram.org/bot123:FAKETOKEN/sendMessage", captured["url"])
        # Тело содержит chat_id и текст с уровнем
        self.assertIn("chat_id=987654321", captured["data"])
        self.assertIn("CRITICAL", captured["data"])
        self.assertIn("%D0%BC%D0%BE%D0%B7%D0%B3", captured["data"])  # URL-encoded «мозг»
        self.assertEqual(captured["method"], "POST")
        # Таймаут из config.ALERT_HTTP_TIMEOUT
        self.assertEqual(captured["timeout"], alerts.config.ALERT_HTTP_TIMEOUT)

    def test_network_failure_falls_back_silently(self):
        # urlopen raise URLError — maybe_alert НЕ прокидывает исключение, пишет fallback в stdout.
        os.environ["KIBORG_ALERT_TOKEN"] = "123:FAKETOKEN"
        os.environ["KIBORG_ALERT_CHAT_ID"] = "987654321"

        def fake_urlopen(req, timeout):
            raise urllib.error.URLError("timed out")

        orig_urlopen = alerts.urllib.request.urlopen
        alerts.urllib.request.urlopen = fake_urlopen
        buf = self._capture_stdout()
        try:
            alerts.maybe_alert("WARN", "сеть упала")  # не должна выбросить
        finally:
            alerts.urllib.request.urlopen = orig_urlopen
        out = buf.getvalue()
        # Fallback-лог: причина видна юзеру, основной текст тоже
        self.assertIn("[ALERT][WARN]", out)
        self.assertIn("сеть упала", out)
        self.assertIn("TG-отправка не удалась", out)

    def test_partial_env_no_token_treated_as_unconfigured(self):
        # Задан только chat_id, токена НЕТ — считаем ненастроенным, fallback на print.
        os.environ.pop("KIBORG_ALERT_TOKEN", None)
        os.environ["KIBORG_ALERT_CHAT_ID"] = "987654321"
        buf = self._capture_stdout()
        alerts.maybe_alert("CRITICAL", "только chat_id без токена")
        out = buf.getvalue()
        self.assertIn("[ALERT][CRITICAL]", out)
        # urlopen НЕ должен был вызваться — иначе тест упал бы (нет мока)


if __name__ == "__main__":
    unittest.main(verbosity=2)
