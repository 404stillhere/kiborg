"""notify.py — уведомления о доставленных идеях в Telegram (без сети).

Проверяем: не падаем без env, не шлём при count=0, формируем корректный текст,
не роняем прогон при сетевой ошибке.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import notify  # noqa: E402


class TestNotify(unittest.TestCase):
    def setUp(self):
        self._orig_token = os.environ.get("KIBORG_NOTIFY_TOKEN")
        self._orig_chat = os.environ.get("KIBORG_NOTIFY_CHAT_ID")
        self._orig_send = notify._send
        self.sent = []
        notify._send = lambda token, chat_id, text: self.sent.append((token, chat_id, text))

    def tearDown(self):
        if self._orig_token is not None:
            os.environ["KIBORG_NOTIFY_TOKEN"] = self._orig_token
        else:
            os.environ.pop("KIBORG_NOTIFY_TOKEN", None)
        if self._orig_chat is not None:
            os.environ["KIBORG_NOTIFY_CHAT_ID"] = self._orig_chat
        else:
            os.environ.pop("KIBORG_NOTIFY_CHAT_ID", None)
        notify._send = self._orig_send

    def _set_env(self):
        os.environ["KIBORG_NOTIFY_TOKEN"] = "token"
        os.environ["KIBORG_NOTIFY_CHAT_ID"] = "123"

    def test_no_env_no_send(self):
        os.environ.pop("KIBORG_NOTIFY_TOKEN", None)
        os.environ.pop("KIBORG_NOTIFY_CHAT_ID", None)
        notify.notify_delivered(2, ["a", "b"])
        self.assertEqual(self.sent, [])

    def test_zero_count_no_send(self):
        self._set_env()
        notify.notify_delivered(0, ["a"])
        self.assertEqual(self.sent, [])

    def test_send_with_titles(self):
        self._set_env()
        notify.notify_delivered(2, ["Идея A", "Идея B"])
        self.assertEqual(len(self.sent), 1)
        token, chat_id, text = self.sent[0]
        self.assertEqual(token, "token")
        self.assertEqual(chat_id, "123")
        self.assertIn("Доставлено идей: 2", text)
        self.assertIn("- Идея A", text)
        self.assertIn("- Идея B", text)

    def test_send_truncates_long_list(self):
        self._set_env()
        titles = [f"Идея {i}" for i in range(15)]
        notify.notify_delivered(15, titles)
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0][2]
        self.assertIn("... и ещё 5", text)
        self.assertEqual(text.count("- Идея"), 10)

    def test_send_error_silenced(self):
        self._set_env()
        notify._send = lambda token, chat_id, text: (_ for _ in ()).throw(RuntimeError("net"))
        notify.notify_delivered(1, ["x"])  # не бросает


if __name__ == "__main__":
    unittest.main(verbosity=2)
