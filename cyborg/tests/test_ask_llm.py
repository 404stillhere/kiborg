"""Тест адаптера ask_llm — БЕЗ сети (organ.js-подпроцесс и keychain замоканы). Контракт
prompt->text по цепочке интуиции (closerouter), снятие ```-заборчика, деградация до "" при
сбое/без ключа, и что wiring._run_ideate подхватывает 'content_llm' (идеи через живую
модель, brain='llm'). Генератор и интуиция теперь на ОДНОЙ цепочке (2026-07-13).
"""
import json
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import ask_llm  # noqa: E402
import wiring  # noqa: E402


class _Proc:
    def __init__(self, stdout, rc=0):
        self.stdout = stdout
        self.returncode = rc
        self.stderr = ""


_CHAIN = [{"id": "deepseek", "baseUrl": "u", "apiKey": "k", "model": "deepseek/deepseek-v4-pro"}]


class TestAskLlm(unittest.TestCase):
    def setUp(self):
        self._orig_run = ask_llm.subprocess.run
        self._orig_chain = ask_llm.keychain.build_chain
        self._orig_exists = ask_llm.os.path.exists
        ask_llm.os.path.exists = lambda p: True         # organ.js «на месте» — сеть всё равно мок

    def tearDown(self):
        ask_llm.subprocess.run = self._orig_run
        ask_llm.keychain.build_chain = self._orig_chain
        ask_llm.os.path.exists = self._orig_exists

    def _chain(self, items=None):
        ask_llm.keychain.build_chain = lambda path=None: _CHAIN if items is None else items

    def _mock_run(self, stdout=None, exc=None, rc=0):
        def fake(cmd, **kw):
            if exc:
                raise exc
            return _Proc(stdout, rc)
        ask_llm.subprocess.run = fake

    def test_strip_fence(self):
        self.assertEqual(ask_llm._strip_fence('```json\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(ask_llm._strip_fence('{"a":1}'), '{"a":1}')

    def test_ask_returns_text_on_success(self):
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": True, "text": '```json\n{"title":"X"}\n```'}))
        out = ask_llm.ask("prompt")
        self.assertIn('"title":"X"', out)
        self.assertNotIn("```", out)                    # заборчик снят

    def test_ask_empty_without_chain(self):
        self._chain([])                                 # ключей нет -> цепочка пуста
        self.assertEqual(ask_llm.ask("prompt"), "")     # даже подпроцесс не зовём

    def test_ask_empty_on_subprocess_error(self):
        self._chain()
        self._mock_run(exc=RuntimeError("node boom"))
        self.assertEqual(ask_llm.ask("prompt"), "")     # сбой -> "" -> вызыватель на stub

    def test_ask_empty_when_not_ok(self):
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": False, "error": "all providers failed"}))
        self.assertEqual(ask_llm.ask("prompt"), "")

    def test_available_reflects_chain(self):
        self._chain([])
        self.assertFalse(ask_llm.available())
        self._chain()
        self.assertTrue(ask_llm.available())

    def test_ideate_uses_content_llm(self):
        canned = ('Идеи:\n{"title":"Идея A","why":"потому","effort":"средне"}\n'
                  '{"title":"Идея B","why":"да","effort":"легко"}')
        out = wiring._run_ideate({"items": [{"title": "hn заголовок"}]},
                                 {"content_llm": lambda p: canned})
        ideas = out["ideas"]
        self.assertTrue(ideas)
        self.assertEqual(ideas[0]["brain"], "llm")      # не stub
        self.assertEqual(ideas[0]["title"], "Идея A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
