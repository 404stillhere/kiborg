"""Тест executor.execute — прод-гейт безопасности киборга (safe_mode).

Executor — тонкий, но КРИТИЧНЫЙ слой: он решает, запускать орган или вернуть skipped
(прод-органы автономно НЕ трогаем; орган без нужного ключа не зовём). Падение органа
ловится в {'error'} для перепланирования, не роняет киборга. Раньше был без теста.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import executor  # noqa: E402


class _Organ:
    """Минимальный орган-двойник: .needs (гейты) + .run(inputs, env)."""

    def __init__(self, needs=None, run_fn=None):
        self.needs = needs
        self._run_fn = run_fn or (lambda inputs, env: {"ok": True})

    def run(self, inputs, env):
        return self._run_fn(inputs, env)


class TestExecutor(unittest.TestCase):
    def test_prod_organ_gated_in_safe_mode(self):
        out = executor.execute(_Organ(needs={"prod": True}), {}, {}, safe_mode=True)
        self.assertIn("skipped", out)
        self.assertIn("prod", out["skipped"])

    def test_prod_organ_runs_when_safe_mode_off(self):
        out = executor.execute(_Organ(needs={"prod": True}), {}, {}, safe_mode=False)
        self.assertEqual(out, {"ok": True})

    def test_key_needed_but_missing_is_skipped(self):
        out = executor.execute(_Organ(needs={"key": "API_KEY"}), {}, {}, safe_mode=True)
        self.assertIn("skipped", out)
        self.assertIn("API_KEY", out["skipped"])

    def test_key_present_via_env_runs(self):
        out = executor.execute(_Organ(needs={"key": "API_KEY"}), {}, {"API_KEY": "v"}, safe_mode=True)
        self.assertEqual(out, {"ok": True})

    def test_key_satisfied_by_llm_callable(self):
        # has_key принимает и прямой ключ, и env['llm'] (орган, которому достаточно модели)
        out = executor.execute(_Organ(needs={"key": "API_KEY"}), {}, {"llm": lambda p: "x"}, safe_mode=True)
        self.assertEqual(out, {"ok": True})

    def test_stub_ok_bypasses_key_gate(self):
        out = executor.execute(_Organ(needs={"key": "API_KEY", "stub_ok": True}), {}, {}, safe_mode=True)
        self.assertEqual(out, {"ok": True})

    def test_key_gate_ignored_when_safe_mode_off(self):
        out = executor.execute(_Organ(needs={"key": "API_KEY"}), {}, {}, safe_mode=False)
        self.assertEqual(out, {"ok": True})

    def test_non_dict_result_wrapped(self):
        out = executor.execute(_Organ(run_fn=lambda i, e: "плоский ответ"), {}, {})
        self.assertEqual(out, {"result": "плоский ответ"})

    def test_organ_exception_caught_as_error_not_crash(self):
        def boom(i, e):
            raise ValueError("сломалось")

        out = executor.execute(_Organ(run_fn=boom), {}, {})
        self.assertIn("error", out)
        self.assertIn("ValueError", out["error"])
        self.assertIn("сломалось", out["error"])

    def test_needs_none_means_no_gates(self):
        out = executor.execute(_Organ(needs=None), {}, {})
        self.assertEqual(out, {"ok": True})

    def test_inputs_and_env_passed_through_untouched(self):
        seen = {}

        def capture(inputs, env):
            seen["inputs"], seen["env"] = inputs, env
            return {"done": True}

        executor.execute(_Organ(run_fn=capture), {"a": 1}, {"b": 2})
        self.assertEqual(seen["inputs"], {"a": 1})
        self.assertEqual(seen["env"], {"b": 2})


if __name__ == "__main__":
    unittest.main(verbosity=2)
