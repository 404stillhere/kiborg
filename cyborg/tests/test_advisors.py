"""Тесты советников (cyborg/advisors.py) — тонкие места без сети/ключей.

Пока: арбитр (RankIdeasAdvisor) прокидывает руль направления из контекста в rank_ideas."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import advisors  # noqa: E402

_OPTS = [{"id": "A", "title": "трекер сна", "why": "x"}, {"id": "B", "title": "агрегатор рецептов", "why": "y"}]


def _capturing_advisor():
    """Возвращает (adv, captured): арбитр RankIdeasAdvisor с fake_rank, пишущим полученный env
    в captured. Вынесено из двух тестов ниже (одинаковый arrange) — убирает дубль."""
    captured = {}

    def fake_rank(inputs, env):
        captured.update(env)
        return {"ideas_best": [dict(inputs["ideas"][0], judged="llm")]}

    return advisors.RankIdeasAdvisor(rank_run=fake_rank), captured


class TestRankAdvisorDirection(unittest.TestCase):
    def test_direction_reaches_rank_env(self):
        adv, captured = _capturing_advisor()
        adv.opine("q", _OPTS, {"content_llm": lambda p: "m", "direction": "игры"})
        self.assertEqual(captured.get("direction"), "игры")  # арбитр судит с рулём

    def test_no_direction_no_key(self):
        adv, captured = _capturing_advisor()
        adv.opine("q", _OPTS, {"content_llm": lambda p: "m"})
        self.assertNotIn("direction", captured)  # без руля ключа нет


class TestAskLlmMaxTokens(unittest.TestCase):
    """Параметр `_MAX_TOKENS` в payload (после дедупа _IntuitionNoCap): родитель кладёт 256,
    подкласс без потолка (None) — не кладёт вовсе. Пиним, чтобы дедуп не поехал по поведению."""

    def _payload_for(self, adv):
        import json as _json

        captured = {}
        adv._js = __file__  # существующий файл -> проходит os.path.exists
        orig = advisors.subprocess.run

        def fake_run(cmd, **kw):
            captured["p"] = _json.loads(kw["input"])
            return type("P", (), {"returncode": 0, "stdout": '{"ok": true, "text": "5"}'})()

        advisors.subprocess.run = fake_run
        try:
            adv._ask(["prov1"], "prompt", 6000)
        finally:
            advisors.subprocess.run = orig
        return captured["p"]

    def test_default_advisor_caps_max_tokens_256(self):
        payload = self._payload_for(advisors.AskLlmAdvisor())
        self.assertEqual(payload["inputs"].get("max_tokens"), 256)

    def test_nocap_subclass_omits_max_tokens(self):
        class _NoCap(advisors.AskLlmAdvisor):
            _MAX_TOKENS = None

        payload = self._payload_for(_NoCap())
        self.assertNotIn("max_tokens", payload["inputs"])  # None -> ключ не кладём (как _IntuitionNoCap)
        self.assertEqual(payload["inputs"]["prompt"], "prompt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
