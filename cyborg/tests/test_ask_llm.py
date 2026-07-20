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
    """Фейк подпроцесса для мока subprocess.Popen. Код ask_llm._run_chain вызывает
    Popen(...).communicate(input=..., timeout=...) и читает proc.returncode/stdout/stderr.
    rc=0+stdout -> успех; rc!=0+пусто -> ""; возбуждение в communicate -> "" (сбой)."""

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
        ask_llm.os.path.exists = lambda p: True  # organ.js «на месте» — сеть всё равно мок

    def tearDown(self):
        ask_llm.subprocess.Popen = self._orig_popen
        ask_llm.keychain.build_chain = self._orig_chain
        ask_llm.os.path.exists = self._orig_exists

    def _chain(self, items=None):
        ask_llm.keychain.build_chain = lambda path=None: _CHAIN if items is None else items

    def _mock_run(self, stdout=None, exc=None, rc=0):
        """Конфигурит subprocess.Popen фейком. exc поднимается из communicate() (сбой node/таймаут)."""
        proc = _Proc(stdout=stdout or "", rc=rc)
        if exc is not None:
            proc.communicate = lambda input=None, timeout=None: (_ for _ in ()).throw(exc)
        ask_llm.subprocess.Popen = lambda cmd, **kw: proc

    def test_strip_fence(self):
        self.assertEqual(ask_llm._strip_fence('```json\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(ask_llm._strip_fence('{"a":1}'), '{"a":1}')

    def test_ask_returns_text_on_success(self):
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": True, "text": '```json\n{"title":"X"}\n```'}))
        out = ask_llm.ask("prompt")
        self.assertIn('"title":"X"', out)
        self.assertNotIn("```", out)  # заборчик снят

    def test_ask_empty_without_chain(self):
        self._chain([])  # ключей нет -> цепочка пуста
        self.assertEqual(ask_llm.ask("prompt"), "")  # даже подпроцесс не зовём

    def test_ask_empty_on_subprocess_error(self):
        self._chain()
        self._mock_run(exc=RuntimeError("node boom"))
        self.assertEqual(ask_llm.ask("prompt"), "")  # сбой -> "" -> вызыватель на stub

    def test_ask_empty_when_not_ok(self):
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": False, "error": "all providers failed"}))
        self.assertEqual(ask_llm.ask("prompt"), "")

    def test_last_provider_set_on_success(self):
        # organ.js возвращает provider (кто РЕАЛЬНО ответил) — цепочка closerouter делает это
        # диагностически полезным (muse-spark=первичная, deepseek/nemotron=фолбэк). ask() ставит
        # last_provider, не ломая контракт callable(prompt)->str.
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": True, "text": '{"title":"X"}', "provider": "muse-spark"}))
        ask_llm.ask("prompt")
        self.assertEqual(ask_llm.last_provider, "muse-spark")  # первичная ответила — видно

    def test_last_provider_cleared_on_failure(self):
        # при сбое (not ok / пусто / exception) last_provider сбрасывается — не врёт «ответил прошлый»
        self._chain()
        self._mock_run(stdout=json.dumps({"ok": True, "text": '{"t":1}', "provider": "deepseek"}))
        ask_llm.ask("prompt")
        self.assertEqual(ask_llm.last_provider, "deepseek")
        self._mock_run(stdout=json.dumps({"ok": False, "error": "boom"}))  # след. вызов упал
        ask_llm.ask("prompt")
        self.assertEqual(ask_llm.last_provider, "")  # сброс, а не зависший «deepseek»

    def test_last_provider_empty_without_chain(self):
        self._chain([])  # нет ключей — спросить некого
        ask_llm.ask("prompt")
        self.assertEqual(ask_llm.last_provider, "")

    def test_available_reflects_chain(self):
        self._chain([])
        self.assertFalse(ask_llm.available())
        self._chain()
        self.assertTrue(ask_llm.available())

    def test_ideate_uses_content_llm(self):
        canned = (
            'Идеи:\n{"title":"Идея A","why":"потому","effort":"средне"}\n'
            '{"title":"Идея B","why":"да","effort":"легко"}'
        )
        out = wiring._run_ideate({"items": [{"title": "hn заголовок"}]}, {"content_llm": lambda p: canned})
        ideas = out["ideas"]
        self.assertTrue(ideas)
        self.assertEqual(ideas[0]["brain"], "llm")  # не stub
        self.assertEqual(ideas[0]["title"], "Идея A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
