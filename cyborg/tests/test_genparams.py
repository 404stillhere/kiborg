"""Тесты параметров генерации идей (cyborg/genparams.py): настраиваемые юзером параметры
(gen_k/rank_keep/source_n/read_min_score/keep_min_score). Дефолт при отсутствии файла,
clamping по диапазонам, частичное сохранение, reset, защита от битого файла/невалидных
типов. Шаблон — test_council_config (тот же скелет _panel_config.load_obj/atomic_save)."""

import json
import math
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import genparams  # noqa: E402


class TestGenparams(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="genparams_")
        self._orig = genparams.PATH
        genparams.PATH = os.path.join(self.tmp, "genparams.json")

    def tearDown(self):
        genparams.PATH = self._orig

    def test_default_when_no_file(self):
        # нет файла → все дефолты из PARAMS (как если бы юзер ни разу не открывал настройки)
        d = genparams.load()
        self.assertEqual(d, genparams.defaults())
        self.assertEqual(d["gen_k"], 8)
        self.assertEqual(d["rank_keep"], 3)
        self.assertEqual(d["source_n"], 105)
        self.assertEqual(d["read_min_score"], 8.0)
        self.assertEqual(d["keep_min_score"], 0.6)

    def test_save_and_read_full(self):
        genparams.save({"gen_k": 12, "rank_keep": 5, "source_n": 50})
        d = genparams.load()
        self.assertEqual(d["gen_k"], 12)
        self.assertEqual(d["rank_keep"], 5)
        self.assertEqual(d["source_n"], 50)
        # не переданные ключи сохраняются (атомарная частичная запись не теряет остальные)
        self.assertEqual(d["read_min_score"], 8.0)

    def test_save_partial_preserves_others(self):
        # частичное обновление НЕ затирает другие поля (ключевая семантика: UI шлёт 1 параметр)
        genparams.save({"gen_k": 12})
        genparams.save({"rank_keep": 4})
        d = genparams.load()
        self.assertEqual(d["gen_k"], 12)
        self.assertEqual(d["rank_keep"], 4)
        self.assertEqual(d["source_n"], 105)  # не тронут

    def test_clamp_above_max(self):
        # значение выше max урезается до max (защита от ручной правки JSON / старой версии)
        genparams.save({"gen_k": 1000})
        self.assertEqual(genparams.load()["gen_k"], 16)

    def test_clamp_below_min(self):
        genparams.save({"rank_keep": -5})
        self.assertEqual(genparams.load()["rank_keep"], 1)

    def test_clamp_float_range(self):
        # read_min_score 0..10, keep_min_score 0..1 — float-диапазоны
        genparams.save({"read_min_score": 15.0, "keep_min_score": 2.5})
        d = genparams.load()
        self.assertEqual(d["read_min_score"], 10.0)
        self.assertEqual(d["keep_min_score"], 1.0)

    def test_invalid_value_falls_back_to_default(self):
        # невалидный тип/строка → default ключа (не падаем, не None)
        genparams.save({"gen_k": "много"})
        self.assertEqual(genparams.load()["gen_k"], 8)  # default

    def test_bool_and_non_finite_values_fall_back_to_defaults(self):
        genparams.save(
            {
                "gen_k": True,
                "read_min_score": float("nan"),
                "keep_min_score": float("inf"),
            }
        )
        d = genparams.load()
        self.assertEqual(d["gen_k"], genparams.defaults()["gen_k"])
        self.assertEqual(d["read_min_score"], genparams.defaults()["read_min_score"])
        self.assertEqual(d["keep_min_score"], genparams.defaults()["keep_min_score"])
        self.assertTrue(all(math.isfinite(float(v)) for v in d.values()))

    def test_rank_keep_never_exceeds_gen_k(self):
        genparams.save({"gen_k": 2, "rank_keep": 8})
        d = genparams.load()
        self.assertEqual(d["gen_k"], 2)
        self.assertEqual(d["rank_keep"], 2)

    def test_hand_edited_file_cross_clamps_rank_keep(self):
        with open(genparams.PATH, "w", encoding="utf-8") as f:
            json.dump({"gen_k": 3, "rank_keep": 8}, f)
        d = genparams.load()
        self.assertEqual(d["rank_keep"], 3)
        self.assertEqual(genparams.meta()["params"]["rank_keep"]["max"], 3)

    def test_non_dict_save_is_safe_noop(self):
        self.assertEqual(genparams.save(["не", "объект"]), genparams.defaults())

    def test_unknown_key_ignored(self):
        # посторонний ключ игнорируется (forward-compat: UI старой версии / лишнее поле)
        genparams.save({"gen_k": 6, "ghost_param": 999})
        d = genparams.load()
        self.assertEqual(d["gen_k"], 6)
        self.assertNotIn("ghost_param", d)

    def test_reset_to_defaults(self):
        # кнопка «↺ сброс» — возвращает все 5 к дефолтам, перезаписывая файл полностью
        genparams.save({"gen_k": 16, "rank_keep": 8, "source_n": 300, "read_min_score": 10.0, "keep_min_score": 1.0})
        d = genparams.reset()
        self.assertEqual(d, genparams.defaults())
        self.assertEqual(genparams.load(), genparams.defaults())

    def test_broken_file_falls_back_to_default(self):
        with open(genparams.PATH, "w", encoding="utf-8") as f:
            f.write("{ не json")
        self.assertEqual(genparams.load(), genparams.defaults())

    def test_non_dict_file_falls_back_to_default(self):
        with open(genparams.PATH, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)  # валидный JSON, но не dict
        self.assertEqual(genparams.load(), genparams.defaults())

    def test_save_atomic_no_tmp_left(self):
        genparams.save({"gen_k": 6})
        with open(genparams.PATH, encoding="utf-8") as f:
            json.load(f)  # валидный JSON
        self.assertFalse(os.path.exists(genparams.PATH + ".tmp"))  # tmp убран os.replace

    def test_meta_has_all_fields_for_ui(self):
        # meta() отдаёт min/max/default/is_float/value для UI (range-инпуты строятся по ним)
        m = genparams.meta()["params"]
        self.assertEqual(set(m.keys()), set(genparams.PARAMS.keys()))
        for key, spec in m.items():
            self.assertIn("min", spec)
            self.assertIn("max", spec)
            self.assertIn("default", spec)
            self.assertIn("is_float", spec)
            self.assertIn("value", spec)
        # keep_min_score — float (0..1), gen_k — int
        self.assertTrue(m["keep_min_score"]["is_float"])
        self.assertFalse(m["gen_k"]["is_float"])

    def test_load_always_returns_all_keys(self):
        # контракт для UI: load ВСЕГДА возвращает полный набор (даже из пустого файла),
        # чтобы _renderGenparams не падал на отсутствующем ключе
        d = genparams.load()
        self.assertEqual(set(d.keys()), set(genparams.PARAMS.keys()))


if __name__ == "__main__":
    unittest.main()
