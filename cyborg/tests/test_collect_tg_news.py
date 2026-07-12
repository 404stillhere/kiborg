"""Тест вендоренного органа collect_tg_news: run(inputs, env) с фейковым клиентом, без сети,
без pyrogram (импорт органа НЕ требует pyrogram — ленивый импорт внутри _make_client)."""
import datetime
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs_vendored import collect_tg_news  # noqa: E402


class FakeMsg:
    def __init__(self, mid, text=None, caption=None, minutes_ago=0):
        self.id = mid
        self.text = text
        self.caption = caption
        self.date = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)


class FakeClient:
    """Дуck-тип pyrogram.Client: контекст-менеджер + get_chat_history (новые -> старые)."""

    def __init__(self, history, broken=()):
        self.history = history
        self.broken = set(broken)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_chat(self, ch):
        pass

    def get_chat_history(self, ch, limit=50):
        if ch in self.broken:
            raise RuntimeError("CHANNEL_PRIVATE")
        return iter(self.history.get(ch, [])[:limit])


class TestCollectTgNews(unittest.TestCase):
    def test_collects_and_reverses_to_chronological(self):
        client = FakeClient({"@a": [FakeMsg(3, "три"), FakeMsg(2, "два"), FakeMsg(1, "один")]})
        result = collect_tg_news.run({"channels": ["@a"]}, {"client": client})
        self.assertEqual([i["id"] for i in result["items"]], [1, 2, 3])
        self.assertEqual(result["items"][0]["url"], "https://t.me/a/1")
        self.assertEqual(result["warnings"], [])

    def test_broken_channel_goes_to_warnings_not_crash(self):
        history = {"@ok": [FakeMsg(1, "жив")]}
        client = FakeClient(history, broken={"@dead"})
        result = collect_tg_news.run({"channels": ["@dead", "@ok"]}, {"client": client})
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(len(result["warnings"]) == 1 and "@dead" in result["warnings"][0])

    def test_private_numeric_channel_has_no_url(self):
        client = FakeClient({"-100123": [FakeMsg(1, "приватный")]})
        result = collect_tg_news.run({"channels": ["-100123"]}, {"client": client})
        self.assertIsNone(result["items"][0]["url"])

    def test_import_has_no_pyrogram_requirement(self):
        self.assertNotIn("pyrogram", getattr(collect_tg_news, "__dict__", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
