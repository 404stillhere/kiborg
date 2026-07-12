"""Тест адаптера ask_llm — БЕЗ сети (urlopen замокан). Контракт prompt->text, снятие
```-заборчика, разбор ответа Gemini, деградация до "" при сбое/без ключа, и что
wiring._run_ideate подхватывает 'content_llm' (идеи идут через живую модель, brain='llm').
"""
import json
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import ask_llm  # noqa: E402
import wiring  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gemini(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class TestAskLlm(unittest.TestCase):
    def setUp(self):
        self._orig_open = ask_llm.urllib.request.urlopen
        self._orig_env = {k: os.environ.get(k) for k in ("GEMINI_KEY", "LLM_KEY")}

    def tearDown(self):
        ask_llm.urllib.request.urlopen = self._orig_open
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _mock(self, payload_or_exc):
        def fake(req, timeout=None):
            if isinstance(payload_or_exc, Exception):
                raise payload_or_exc
            return _Resp(payload_or_exc)
        ask_llm.urllib.request.urlopen = fake

    def test_strip_fence(self):
        self.assertEqual(ask_llm._strip_fence('```json\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(ask_llm._strip_fence('{"a":1}'), '{"a":1}')

    def test_ask_returns_text_on_success(self):
        os.environ["GEMINI_KEY"] = "testkey"
        self._mock(_gemini('```json\n{"title":"X","why":"y","effort":"легко"}\n```'))
        out = ask_llm.ask("prompt")
        self.assertIn('"title":"X"', out)
        self.assertNotIn("```", out)

    def test_ask_empty_without_key(self):
        os.environ.pop("GEMINI_KEY", None)
        os.environ.pop("LLM_KEY", None)
        orig = ask_llm._KEY_FILE
        ask_llm._KEY_FILE = "M:/nope/none.md"      # не подхватывать реальный gemini.md
        try:
            self.assertEqual(ask_llm.ask("prompt"), "")
        finally:
            ask_llm._KEY_FILE = orig

    def test_ask_empty_on_network_error(self):
        os.environ["GEMINI_KEY"] = "testkey"
        self._mock(RuntimeError("boom"))
        self.assertEqual(ask_llm.ask("prompt"), "")   # сбой -> "" -> вызыватель на stub

    def test_ideate_uses_content_llm(self):
        canned = ('Идеи:\n{"title":"Идея A","why":"потому","effort":"средне"}\n'
                  '{"title":"Идея B","why":"да","effort":"легко"}')
        out = wiring._run_ideate({"items": [{"title": "hn заголовок"}]},
                                 {"content_llm": lambda p: canned})
        ideas = out["ideas"]
        self.assertTrue(ideas)
        self.assertEqual(ideas[0]["brain"], "llm")     # не stub
        self.assertEqual(ideas[0]["title"], "Идея A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
