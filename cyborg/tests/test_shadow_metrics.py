"""Тесты C4 shadow_metrics — append-only журнал «что было бы» для lazy orchestra.

Shadow-режим НЕ меняет поведение конвейера — только пишет записи о том, вызвал бы
lazy orchestra Фазу 2 (расхождение советников) или нет. Данные для решения
«включать ли A2 в бою».
"""

import os
import tempfile
import unittest

import shadow_metrics


class TestShadowMetricsAppendLoad(unittest.TestCase):
    """Запись и чтение jsonl. Только stdlib, файл-персист атомарно."""

    def setUp(self):
        self._orig_path = shadow_metrics.PATH
        d = os.path.join(tempfile.gettempdir(), "kiborg_sm_tests")
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(d, "sm_test.jsonl")
        shadow_metrics.PATH = self.path
        self._cleanup()

    def tearDown(self):
        shadow_metrics.PATH = self._orig_path
        self._cleanup()

    def _cleanup(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_load_empty_when_no_file(self):
        # файла нет → пустой список (не исключение)
        self.assertEqual(shadow_metrics.load(), [])

    def test_append_then_load_roundtrip(self):
        # одна запись пишется и читается обратно со всеми полями
        shadow_metrics.append(
            {
                "overlap": 0.667,
                "would_call_phase2": False,
                "top_rank": [0, 1, 2],
                "top_ask": [0, 1, 2],
                "n_ideas": 8,
                "n_reviewers": 6,
            }
        )
        recs = shadow_metrics.load()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertAlmostEqual(rec["overlap"], 0.667)
        self.assertFalse(rec["would_call_phase2"])
        self.assertEqual(rec["top_rank"], [0, 1, 2])
        self.assertEqual(rec["n_ideas"], 8)
        self.assertIn("ts", rec)  # ts проставляется автоматически

    def test_append_multiple_records_in_order(self):
        # несколько записей — порядок сохранён
        for i in range(3):
            shadow_metrics.append({"overlap": i * 0.1, "would_call_phase2": i == 2, "n_ideas": 5})
        recs = shadow_metrics.load()
        self.assertEqual(len(recs), 3)
        self.assertEqual([r["overlap"] for r in recs], [0.0, 0.1, 0.2])

    def test_ts_auto_added_if_absent(self):
        # ts нет в записи → проставляется
        shadow_metrics.append({"overlap": 0.5, "would_call_phase2": True})
        rec = shadow_metrics.load()[0]
        self.assertIn("ts", rec)
        self.assertIsInstance(rec["ts"], str)

    def test_ts_preserved_if_present(self):
        # ts уже есть → не перезаписывается
        shadow_metrics.append({"overlap": 0.5, "would_call_phase2": True, "ts": "2026-01-01T00:00:00"})
        rec = shadow_metrics.load()[0]
        self.assertEqual(rec["ts"], "2026-01-01T00:00:00")

    def test_non_dict_input_silently_ignored(self):
        # не-словарь → тихо игнорируется (не падает, не пишет)
        shadow_metrics.append("not a dict")  # type: ignore[arg-type]
        shadow_metrics.append(None)  # type: ignore[arg-type]
        shadow_metrics.append(123)  # type: ignore[arg-type]
        self.assertEqual(shadow_metrics.load(), [])

    def test_load_skips_broken_lines(self):
        # в файле битые строки вперемешку с валидными → валидные читаются, битые пропущены
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"overlap": 0.5, "would_call_phase2": true}\n')
            f.write("this is not json\n")
            f.write('{"overlap": 0.9, "would_call_phase2": false}\n')
            f.write("\n")  # пустая строка
            f.write("{broken json without closing\n")
        recs = shadow_metrics.load()
        self.assertEqual(len(recs), 2)
        self.assertEqual([r["overlap"] for r in recs], [0.5, 0.9])


if __name__ == "__main__":
    unittest.main()
