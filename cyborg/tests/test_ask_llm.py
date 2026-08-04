"""Тест адаптера ask_llm — БЕЗ сети (zai и organ.js-подпроцесс замоканы). Контракт
prompt->text: сначала z.ai, при сбое/отсутствии ключа — fallback-цепочка closerouter.
Проверяем снятие ```-заборчика, деградацию до "" при сбое, last_provider,
и что wiring._run_ideate подхватывает 'content_llm'.
"""

import json
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import ask_llm  # noqa: E402
import wiring  # noqa: E402
import zai_ask  # noqa: E402


class _Proc:
    def __init__(self, stdout="", rc=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = rc

    def communicate(self, input=None, timeout=None):
        return self.stdout, self.stderr


_CHAIN = [{"id": "deepseek", "baseUrl": "u", "apiKey": "k", "model": "deepseek/deepseek-v4-pro"}]


class TestAskLlm(unittest.TestCase):
    def setUp(self):
        self._orig_popen = ask_llm.subprocess.Popen
        self._orig_chain = ask_llm.keychain.build_chain
        self._orig_exists = ask_llm.os.path.exists
        self._orig_zai_available = zai_ask.available
        self._orig_zai_ask = zai_ask.ask
        ask_llm.os.path.exists = lambda p: True
        ask_llm.last_provider = ""

    def tearDown(self):
        ask_llm.subprocess.Popen = self._orig_popen
        ask_llm.keychain.build_chain = self._orig_chain
        ask_llm.os.path.exists = self._orig_exists
        zai_ask.available = self._orig_zai_available
        zai_ask.ask = self._orig_zai_ask

    def _chain(self, items=None):
        ask_llm.keychain.build_chain = lambda path=None: _CHAIN if items is None else items

    def _mock_run(self, stdout=None, exc=None, rc=0):
        proc = _Proc(stdout=stdout or "", rc=rc)
        if exc is not None:
            proc.communicate = lambda input=None, timeout=None: (_ for _ in ()).throw(exc)
        ask_llm.subprocess.Popen = lambda cmd, **kw: proc

    def _no_zai(self):
        zai_ask.available = lambda: False

    def _yes_zai(self, text=""):
        zai_ask.available = lambda: True
        zai_ask.ask = lambda prompt, timeout_ms=None, max_tokens=8192, temperature=0.9: text

    def test_strip_fence(self):
        self.assertEqual(ask_llm._strip_fence('```json\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(ask_llm._strip_fence('{"a":1}'), '{"a":1}')

    def test_ask_prefers_zai_when_available(self):
        self._yes_zai('{"title":"Z"}')
        out = ask_llm.ask("prompt")
        self.assertEqual(out, '{"title":"Z"}')
        self.assertEqual(ask_llm.last_provider, "zai")

    def test_ask_falls_back_to_chain_when_zai_empty(self):
        self._yes_zai("")
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": True, "text": '{"title":"X"}'}))
        out = ask_llm.ask("prompt")
        self.assertEqual(out, '{"title":"X"}')

    def test_ask_returns_text_on_success(self):
        self._no_zai()
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": True, "text": '```json\n{"title":"X"}\n```'}))
        out = ask_llm.ask("prompt")
        self.assertIn('"title":"X"', out)
        self.assertNotIn("```", out)

    def test_ask_empty_without_chain(self):
        self._no_zai()
        self._chain([])
        self.assertEqual(ask_llm.ask("prompt"), "")

    def test_ask_empty_on_subprocess_error(self):
        self._no_zai()
        self._chain()
        self._mock_run(exc=RuntimeError("node boom"))
        self.assertEqual(ask_llm.ask("prompt"), "")

    def test_ask_empty_when_not_ok(self):
        self._no_zai()
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": False, "error": "all providers failed"}))
        self.assertEqual(ask_llm.ask("prompt"), "")

    def test_last_provider_set_on_success(self):
        self._no_zai()
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": True, "text": '{"title":"X"}', "provider": "muse-spark"}))
        ask_llm.ask("prompt")
        self.assertEqual(ask_llm.last_provider, "muse-spark")

    def test_last_provider_cleared_on_failure(self):
        self._no_zai()
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": True, "text": '{"t":1}', "provider": "deepseek"}))
        ask_llm.ask("prompt")
        self.assertEqual(ask_llm.last_provider, "deepseek")
        self._mock_run(stdout=json.dumps({"ok": False, "error": "boom"}))
        ask_llm.ask("prompt")
        self.assertEqual(ask_llm.last_provider, "")

    def test_last_provider_empty_without_chain(self):
        self._no_zai()
        self._chain([])
        ask_llm.ask("prompt")
        self.assertEqual(ask_llm.last_provider, "")

    def test_available_reflects_chain(self):
        self._no_zai()
        self._chain([])
        self.assertFalse(ask_llm.available())
        self._chain()
        self.assertTrue(ask_llm.available())

    def test_available_reflects_zai(self):
        self._chain([])
        zai_ask.available = lambda: True
        self.assertTrue(ask_llm.available())

    def test_ideate_uses_content_llm(self):
        canned = (
            'Идеи:\n{"title":"Идея A","why":"потому","effort":"средне"}\n'
            '{"title":"Идея B","why":"да","effort":"легко"}'
        )
        out = wiring._run_ideate({"items": [{"title": "hn заголовок"}]}, {"content_llm": lambda p: canned})
        ideas = out["ideas"]
        self.assertTrue(ideas)
        self.assertEqual(ideas[0]["brain"], "llm")
        self.assertEqual(ideas[0]["title"], "Идея A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
