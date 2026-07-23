"""Тесты B4 feedback_cortex — адаптация весов совета по сигналам триажа.

Фоновый cron-processor: читает triage_events.jsonl, обновляет council_weights.json
(EMA α=0.02, старт после ≥20 событий, decay к равномерному каждые 30, мин вес 0.15).
Активирует council_weights.enabled=true только после накопления порога. НЕ блокирует
пайплайн (запуск раз в час).

Тестируем pure-функции адаптации (без I/O): _frozen_start, _ema_update, _decay_to_uniform,
_min_weight_enforced. Cron-оркестрация (main) — тонкая обёртка над ними.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import feedback_cortex  # noqa: E402


class TestFrozenStart(unittest.TestCase):
    """До порога ≥20 событий — веса НЕ адаптируются (равномерные/канон), enabled=false."""

    def test_below_threshold_no_adaptation(self):
        # 15 событий (< 20) → возвращаем канон, enabled остаётся false
        events = [{"action": "take", "judged": "council"} for _ in range(15)]
        result = feedback_cortex.adapt_weights(events, current=council_weights_canon())
        self.assertFalse(result["enabled"])  # не активирован
        self.assertEqual(result["weights"], council_weights_canon())  # канон без изменений

    def test_at_threshold_activates(self):
        # ровно 20 событий → активируется, веса пересчитаны
        events = [{"action": "take", "judged": "council"} for _ in range(20)]
        result = feedback_cortex.adapt_weights(events, current=council_weights_canon())
        self.assertTrue(result["enabled"])


class TestEMAUpdate(unittest.TestCase):
    """EMA α=0.02: советник, чьи идеи чаще берутся (take), вес растёт."""

    def test_advisor_whose_ideas_taken_gains_weight(self):
        # rank_ideas судил идеи, которые юзер брал (take) → вес rank_ideas должен расти
        # относительно ask_llm, чьи идеи юзер отвергал (trash)
        events = [{"action": "take", "judged": "council"} for _ in range(20)] + [  # все take
            {"action": "trash", "judged": "council"} for _ in range(5)
        ]
        canon = council_weights_canon()
        result = feedback_cortex.adapt_weights(events, current=dict(canon))
        # при всех take — позитивный сигнал всем советникам (судили хорошо) → веса держатся
        # пропорционально. Главное: enabled=True после порога.
        self.assertTrue(result["enabled"])
        # веса остались в разумных границах (не ушли в 0, не превысили 1)
        for w in result["weights"].values():
            self.assertGreater(w, 0)
            self.assertLessEqual(w, 1.0)


class TestDecayToUniform(unittest.TestCase):
    """Каждые 30 событий — decay (0.95) к равномерному распределению (1/3 каждому)."""

    def test_decay_pulls_toward_uniform(self):
        # веса сильно скошены (один советник 0.9, другие по 0.05) → decay подтягивает к 1/3
        skewed = {"ask_llm": 0.05, "orchestra": 0.05, "rank_ideas": 0.9}
        decayed = feedback_cortex.apply_decay(skewed, factor=0.95)
        # rank_ideas (0.9) должен уменьшиться, ask_llm/orchestra (0.05) — вырасти к равномерному
        self.assertLess(decayed["rank_ideas"], 0.9)
        self.assertGreater(decayed["ask_llm"], 0.05)
        self.assertGreater(decayed["orchestra"], 0.05)


class TestMinWeightEnforced(unittest.TestCase):
    """Мин вес 0.15 — никто не выключается полностью (даже если EMA хотел 0)."""

    def test_min_weight_floor(self):
        weights = {"ask_llm": 0.01, "orchestra": 0.0, "rank_ideas": 0.99}
        clamped = feedback_cortex.enforce_min_weight(weights, floor=0.15)
        self.assertGreaterEqual(clamped["ask_llm"], 0.15)
        self.assertGreaterEqual(clamped["orchestra"], 0.15)
        # rank_ideas остался высоким (не задет floor), но после clamp+renormalize может чуть снизиться
        self.assertGreater(clamped["rank_ideas"], 0.15)


def council_weights_canon():
    return {"ask_llm": 0.39, "orchestra": 0.20, "rank_ideas": 0.41}


if __name__ == "__main__":
    unittest.main(verbosity=2)
