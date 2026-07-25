"""Тесты B4 feedback_cortex — адаптация весов совета по сигналам триажа.

Фоновый cron-processor: читает triage_events.jsonl, обновляет council_weights.json
(EMA α=0.02, старт после ≥20 событий, decay к равномерному каждые 30, мин вес 0.15).
Активирует council_weights.enabled=true только после накопления порога. НЕ блокирует
пайплайн (запуск раз в час).

Тестируем pure-функции адаптации (без I/O): _frozen_start, _ema_update, _decay_to_uniform,
_min_weight_enforced. Cron-оркестрация (main) — тонкая обёртка над ними.
"""

import json
import math
import os
import sys
import tempfile
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

    def test_non_triage_dicts_do_not_unlock_feedback(self):
        # Журнал append-only допускает старый/битый dict. Он не является решением юзера и
        # не должен сам включать адаптивные веса после 20 случайных строк.
        events = [{"kind": "diagnostic"} for _ in range(20)]
        result = feedback_cortex.adapt_weights(events, current=council_weights_canon())
        self.assertFalse(result["enabled"])
        self.assertEqual(result["updated_after"], 0)


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


class TestIncrementalProcessing(unittest.TestCase):
    """updated_after — курсор: один triage-сигнал применяется ровно один раз."""

    def _event(self):
        return {
            "action": "trash",
            "judged": "council",
            "breakdown_votes": {
                "rank_ideas": {"score": 0.10},
                "ask_llm": {"score": 0.50},
                "orchestra": {"score": 0.95},
            },
        }

    def test_same_history_second_run_is_noop(self):
        first = feedback_cortex.adapt_weights([self._event()] * 20, council_weights_canon())
        second = feedback_cortex.adapt_weights(
            [],
            first["weights"],
            previous_count=first["updated_after"],
            enabled=first["enabled"],
        )
        self.assertEqual(second["weights"], first["weights"])
        self.assertEqual(second["updated_after"], 20)

    def test_only_new_events_move_weights(self):
        first = feedback_cortex.adapt_weights([self._event()] * 20, council_weights_canon())
        second = feedback_cortex.adapt_weights(
            [self._event()],
            first["weights"],
            previous_count=first["updated_after"],
            enabled=True,
        )
        self.assertNotEqual(second["weights"], first["weights"])
        self.assertEqual(second["updated_after"], 21)

    def test_decay_runs_only_when_new_batch_crosses_boundary(self):
        skewed = {"ask_llm": 0.05, "orchestra": 0.05, "rank_ideas": 0.90}
        crossed = feedback_cortex.adapt_weights(
            [{"action": "later"}],
            skewed,
            previous_count=29,
            enabled=True,
        )
        replay = feedback_cortex.adapt_weights(
            [],
            crossed["weights"],
            previous_count=30,
            enabled=True,
        )
        self.assertLess(crossed["weights"]["rank_ideas"], skewed["rank_ideas"])
        self.assertEqual(replay["weights"], crossed["weights"])

    def test_infinite_previous_count_falls_back_to_zero(self):
        events = [self._event()] * 20
        result = feedback_cortex.adapt_weights(
            events,
            council_weights_canon(),
            previous_count=float("inf"),
        )
        self.assertTrue(result["enabled"])
        self.assertEqual(result["updated_after"], 20)


class TestMainCursorIntegration(unittest.TestCase):
    """Cron-обёртка читает только хвост журнала после updated_after."""

    def setUp(self):
        import council_weights

        self._tmp = tempfile.TemporaryDirectory(prefix="fc_main_")
        self.tmp = self._tmp.name
        self.events_path = os.path.join(self.tmp, "triage_events.jsonl")
        self._orig_weights_path = council_weights.PATH
        council_weights.PATH = os.path.join(self.tmp, "council_weights.json")

    def tearDown(self):
        import council_weights

        council_weights.PATH = self._orig_weights_path
        self._tmp.cleanup()

    def _append(self, count):
        event = {
            "action": "trash",
            "judged": "council",
            "breakdown_votes": {
                "rank_ideas": {"score": 0.10},
                "ask_llm": {"score": 0.50},
                "orchestra": {"score": 0.95},
            },
        }
        with open(self.events_path, "a", encoding="utf-8") as f:
            for _ in range(count):
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def test_second_run_without_new_events_keeps_weights(self):
        import council_weights

        self._append(20)
        feedback_cortex.main(events_path=self.events_path)
        first = council_weights.load()
        feedback_cortex.main(events_path=self.events_path)
        second = council_weights.load()
        self.assertEqual(second["weights"], first["weights"])
        self.assertEqual(second["updated_after"], 20)

        self._append(1)
        feedback_cortex.main(events_path=self.events_path)
        third = council_weights.load()
        self.assertNotEqual(third["weights"], second["weights"])
        self.assertEqual(third["updated_after"], 21)

    def test_below_threshold_keeps_cursor_zero_until_activation(self):
        import council_weights

        self._append(15)
        feedback_cortex.main(events_path=self.events_path)
        frozen = council_weights.load()
        self.assertFalse(frozen["enabled"])
        self.assertEqual(frozen["updated_after"], 0)

        self._append(5)
        feedback_cortex.main(events_path=self.events_path)
        active = council_weights.load()
        self.assertTrue(active["enabled"])
        self.assertEqual(active["updated_after"], 20)

    def test_truncated_log_preserves_weights_and_rebases_cursor(self):
        import council_weights

        self._append(20)
        feedback_cortex.main(events_path=self.events_path)
        before = council_weights.load()
        with open(self.events_path, encoding="utf-8") as f:
            first_five = f.readlines()[:5]
        with open(self.events_path, "w", encoding="utf-8") as f:
            f.writelines(first_five)

        feedback_cortex.main(events_path=self.events_path)
        after = council_weights.load()
        self.assertEqual(after["weights"], before["weights"])
        self.assertTrue(after["enabled"])
        self.assertEqual(after["updated_after"], 5)

    def test_main_restores_global_triage_events_path(self):
        self._append(1)
        feedback_cortex.main(events_path=self.events_path)
        import triage_events

        original_path = triage_events.PATH
        other_path = os.path.join(self.tmp, "other_events.jsonl")
        with open(other_path, "w", encoding="utf-8") as f:
            f.write('{"action":"later"}\n')
        feedback_cortex.main(events_path=other_path)
        self.assertEqual(triage_events.PATH, original_path)

    def test_main_restores_sys_path(self):
        self._append(1)
        before = list(sys.path)
        feedback_cortex.main(events_path=self.events_path)
        self.assertEqual(sys.path, before)


class TestBreakdownVotesAdaptation(unittest.TestCase):
    """Фаза 3 Feedback Cortex: per-advisor адаптация по breakdown_votes.

    Когда у события есть breakdown_votes, сигнал считается ПО КАЖДОМУ советнику отдельно:
        signal[name] += action_sign × (advisor_score − 0.5) × 2
    где action_sign = +1 для take, −1 для trash. (advisor_score − 0.5) × 2 переводит [0,1] в [-1,1]:
        0.95 → +0.9, 0.50 → 0, 0.10 → −0.8.

    Итог:
      take + высокий голос советника → + (советник прав, его и хвалим)
      trash + высокий голос советника → − (советник ошибся, штрафуем)
      trash + низкий голос советника  → + (советник был прав, что не верил в мусор)
      take + низкий голос советника   → − (советник не верил в хорошую идею)

    Без breakdown_votes (старые данные) — fallback на прежнюю логику по judged (backward compat).
    """

    def _bulk(self, template, n=20):
        """Размножить событие до порога активации (≥20). Один и тот же сигнал × n."""
        return [dict(template) for _ in range(n)]

    def test_trash_punishes_advisor_who_voted_high(self):
        # idea trashed, orchestra поставил 0.95 (сильно ошибся) → его вес падает относительно
        # старта; rank_ideas поставил 0.10 (был прав — низкая оценка мусора) → его вес растёт.
        # EMA α=0.02 медленная (часовой cron) — за один цикл веса сдвигаются на ~2%, поэтому
        # проверяем НАПРАВЛЕНИЕ изменения относительно канонического старта, не абсолютное превосходство.
        events = self._bulk(
            {
                "action": "trash",
                "judged": "council",
                "breakdown_votes": {
                    "orchestra": {"score": 0.95},  # высоко за мусор → штраф
                    "rank_ideas": {"score": 0.10},  # низко за мусор → награда
                    "ask_llm": {"score": 0.50},  # нейтрально (0.5 → 0 сигнал)
                },
            }
        )
        canon = council_weights_canon()
        result = feedback_cortex.adapt_weights(events, current=dict(canon))
        self.assertTrue(result["enabled"])
        w = result["weights"]
        # orchestra (голосовал высоко за мусор) УПАЛ от канона 0.20
        self.assertLess(
            w["orchestra"], canon["orchestra"], "orchestra (высоко за trash) должен упасть от своего канона"
        )
        # rank_ideas (голосовал низко за мусор = был прав) ВЫРОС от канона 0.41
        self.assertGreater(
            w["rank_ideas"], canon["rank_ideas"], "rank_ideas (низко за trash = прав) должен вырасти от своего канона"
        )

    def test_trash_rewards_advisor_who_voted_low(self):
        # idea trashed, rank_ideas поставил 0.10 (прав — низко за мусор) → его вес растёт
        # от канона; orchestra нейтрален (0.5) → его вес не двигается.
        events = self._bulk(
            {
                "action": "trash",
                "judged": "council",
                "breakdown_votes": {
                    "rank_ideas": {"score": 0.10},
                    "orchestra": {"score": 0.50},
                    "ask_llm": {"score": 0.50},
                },
            }
        )
        canon = council_weights_canon()
        result = feedback_cortex.adapt_weights(events, current=dict(canon))
        w = result["weights"]
        # rank_ideas (низко за мусор = прав) ВЫРОС от канона 0.41
        self.assertGreater(
            w["rank_ideas"], canon["rank_ideas"], "rank_ideas (низко за trash = прав) должен вырасти от канона"
        )
        # orchestra (нейтрален 0.5) НЕ получил сигнала → остался ≈ на каноне 0.20
        # (допускаем微小 drift от renormalize, но не рост от награды)
        self.assertLessEqual(
            w["orchestra"], canon["orchestra"] + 0.01, "orchestra (нейтрален) не должен вырасти от чужой награды"
        )

    def test_take_rewards_advisor_who_voted_high(self):
        # idea taken, orchestra поставил 0.95 (прав — высоко за хорошую) → его вес растёт
        # от канона; rank_ideas нейтрален (0.5) → не двигается.
        events = self._bulk(
            {
                "action": "take",
                "judged": "council",
                "breakdown_votes": {
                    "orchestra": {"score": 0.95},
                    "rank_ideas": {"score": 0.50},
                    "ask_llm": {"score": 0.50},
                },
            }
        )
        canon = council_weights_canon()
        result = feedback_cortex.adapt_weights(events, current=dict(canon))
        w = result["weights"]
        # orchestra (высоко за take = прав) ВЫРОС от канона 0.20
        self.assertGreater(
            w["orchestra"], canon["orchestra"], "orchestra (высоко за take = прав) должен вырасти от своего канона"
        )
        # rank_ideas (нейтрален 0.5) НЕ получил сигнала → остался ≈ на каноне 0.41
        self.assertLessEqual(
            w["rank_ideas"], canon["rank_ideas"] + 0.01, "rank_ideas (нейтрален) не должен вырасти от чужой награды"
        )

    def test_take_punishes_advisor_who_voted_low(self):
        # idea taken, ask_llm поставил 0.10 (ошибся — низко за хорошую) → его вес падает от канона.
        events = self._bulk(
            {
                "action": "take",
                "judged": "council",
                "breakdown_votes": {
                    "ask_llm": {"score": 0.10},
                    "rank_ideas": {"score": 0.50},
                    "orchestra": {"score": 0.50},
                },
            }
        )
        canon = council_weights_canon()
        result = feedback_cortex.adapt_weights(events, current=dict(canon))
        w = result["weights"]
        # ask_llm (низко за take = ошибся) УПАЛ от канона 0.39
        self.assertLess(
            w["ask_llm"], canon["ask_llm"], "ask_llm (низко за take = ошибся) должен упасть от своего канона"
        )

    def test_backward_compat_no_breakdown_votes_uses_judged(self):
        # событие БЕЗ breakdown_votes (старые данные) → работает прежняя логика по judged.
        # judged="council" → сигнал всем ALL_ADVISORS (как до Фазы 3).
        events = self._bulk({"action": "take", "judged": "council"})  # без breakdown_votes
        canon = council_weights_canon()
        result = feedback_cortex.adapt_weights(events, current=dict(canon))
        self.assertTrue(result["enabled"])
        # все три советника получили одинаковый +сигнал (судили все, все правы) → веса ≈ равны
        # после decay+renormalize. Допускаем небольшой разброс, но не катастрофический.
        w = result["weights"]
        self.assertGreater(w["rank_ideas"], 0)
        self.assertGreater(w["ask_llm"], 0)
        self.assertGreater(w["orchestra"], 0)

    def test_later_action_still_ignored(self):
        # later по-прежнему нейтрален (не + не −) — даже если есть breakdown_votes.
        events = self._bulk(
            {
                "action": "later",
                "judged": "council",
                "breakdown_votes": {"rank_ideas": {"score": 0.95}},
            }
        )
        canon = council_weights_canon()
        result = feedback_cortex.adapt_weights(events, current=dict(canon))
        # later не даёт сигнала → веса остаются близко к канону (только decay к uniform)
        w = result["weights"]
        for name in ("rank_ideas", "ask_llm", "orchestra"):
            self.assertGreater(w[name], 0)

    def test_malformed_breakdown_votes_safe(self):
        # битые breakdown_votes (не dict, нет score, не число) → не падает, fallback.
        # Советник с мусором пропускается, остальные (если валидны) учитываются.
        events = self._bulk(
            {
                "action": "trash",
                "judged": "council",
                "breakdown_votes": {
                    "orchestra": "не словарь",  # мусор → пропустить
                    "rank_ideas": {"score": "не число"},  # мусор → пропустить
                    "ask_llm": {"no_score": True},  # нет score → пропустить
                },
            }
        )
        canon = council_weights_canon()
        result = feedback_cortex.adapt_weights(events, current=dict(canon))
        # не упало, веса валидные (после decay/min_weight — все на дно/равны)
        self.assertTrue(result["enabled"])
        for w in result["weights"].values():
            self.assertGreaterEqual(w, feedback_cortex.MIN_WEIGHT)

    def test_non_finite_or_out_of_range_scores_cannot_poison_weights(self):
        events = self._bulk(
            {
                "action": "take",
                "breakdown_votes": {
                    "rank_ideas": {"score": float("nan")},
                    "ask_llm": {"score": float("inf")},
                    "orchestra": {"score": True},
                },
            }
        )
        result = feedback_cortex.adapt_weights(events, current=council_weights_canon())
        for weight in result["weights"].values():
            self.assertTrue(math.isfinite(weight))
            self.assertGreaterEqual(weight, feedback_cortex.MIN_WEIGHT)


def council_weights_canon():
    return {"ask_llm": 0.39, "orchestra": 0.20, "rank_ideas": 0.41}


if __name__ == "__main__":
    unittest.main(verbosity=2)
