"""Тесты гейта «источник изменился?» в harvest — чтобы не гонять LLM впустую.

Фиксируем:
  1. Отпечаток заголовков: порядок НЕ важен, изменение состава — важно.
  2. Персист сигнатуры: _save_sig -> _last_sig возвращает то же (атомарно, во временную папку).
"""
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import harvest  # noqa: E402
import seen_items  # noqa: E402


class TestHarvestGate(unittest.TestCase):
    def test_titles_sig_order_independent_change_sensitive(self):
        a = harvest._titles_sig(["Идея А", "Идея Б", "Идея В"])
        b = harvest._titles_sig(["Идея В", "Идея А", "Идея Б"])  # тот же набор, другой порядок
        self.assertEqual(a, b)                                   # порядок не важен
        c = harvest._titles_sig(["Идея А", "Идея Б", "Идея Г"])  # состав изменился
        self.assertNotEqual(a, c)                                # изменение поймано

    def test_sig_persist_roundtrip(self):
        tmp = tempfile.mkdtemp(prefix="harvest_")
        orig = harvest.STATE_FILE
        harvest.STATE_FILE = os.path.join(tmp, "harvest_state.json")
        try:
            self.assertIsNone(harvest._last_sig())      # пусто -> None
            harvest._save_sig("deadbeef")
            self.assertEqual(harvest._last_sig(), "deadbeef")
            self.assertFalse(os.path.exists(harvest.STATE_FILE + ".tmp"))  # атомарно, без хвоста
        finally:
            harvest.STATE_FILE = orig

    def test_harvest_env_widens_source(self):
        # КОРЕНЬ узкого источника: env харвеста должен тянуть шире дефолтных 8 заголовков
        env = harvest._harvest_env()
        self.assertEqual(env["n"], harvest.SOURCE_N)
        self.assertGreater(harvest.SOURCE_N, 8)  # шире дефолта органа collect_source

    def test_harvest_env_carries_configured_sources(self):
        # env харвеста несёт ТЕКУЩИЙ список источников из harvest.SOURCES — сколько бы их
        # ни было (1 или 5). Тест не называет источники по именам, чтобы не красить
        # каждый раз, когда состав SOURCES меняется (было один раз с 5 на 1).
        env = harvest._harvest_env()
        self.assertEqual(env["sources"], harvest.SOURCES)
        self.assertGreater(len(harvest.SOURCES), 0)

    def test_harvest_env_requests_seen_items_filter(self):
        # трекер «уже видели» (по ID items) — включён ТОЛЬКО для харвеста, не для интерактива
        env = harvest._harvest_env()
        self.assertTrue(env["filter_seen_items"])

    def test_source_signature_uses_wide_n(self):
        # гейт «лента изменилась?» снимает отпечаток на ТОЙ ЖЕ ширине, что и прогон (без сети)
        from organs import collect_source
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [{"title": "A"}, {"title": "B"}], "degraded": False}

        orig = collect_source.run
        collect_source.run = fake_run
        try:
            sig, degraded, fresh_n, status = harvest._source_signature()
        finally:
            collect_source.run = orig
        self.assertEqual(captured.get("n"), harvest.SOURCE_N)  # гейт и прогон смотрят одинаково глубоко
        self.assertEqual(captured.get("sources"), harvest.SOURCES)  # и по тому же набору источников
        self.assertIsNotNone(sig)
        self.assertFalse(degraded)
        self.assertEqual(fresh_n, 2)  # items без id -> дедуп невозможен -> считаются свежими всегда
        self.assertEqual(set(status["sources"]), set(harvest.SOURCES))  # статус покрывает все источники

    def test_source_signature_covers_union_not_just_hn(self):
        # отпечаток должен реагировать на изменение в ЛЮБОМ источнике, не только HN — иначе
        # gate соврёт «не изменилось» при реальном churn в reddit/lobsters/gh_trending.
        from organs import collect_source
        calls = []

        def fake_run(inputs, env):
            calls.append(1)
            # первый вызов "старая" reddit-идея, второй — "новая" (HN-часть неизменна)
            title = "reddit idea v1" if len(calls) == 1 else "reddit idea v2"
            return {"items": [{"title": "hn idea"}, {"title": title}], "degraded": False}

        orig = collect_source.run
        collect_source.run = fake_run
        try:
            sig1, _, _, _ = harvest._source_signature()
            sig2, _, _, _ = harvest._source_signature()
        finally:
            collect_source.run = orig
        self.assertNotEqual(sig1, sig2)

    def test_source_signature_fresh_n_via_seen_items(self):
        # fresh_n считает по ID (не по тексту) и НЕ мутирует seen-файл (это только gate-проверка)
        from organs import collect_source
        orig_path = seen_items.PATH
        tmp = tempfile.mkdtemp(prefix="harvest_fresh_")
        seen_items.PATH = os.path.join(tmp, "seen_items.json")

        def fake_run(inputs, env):
            return {"items": [{"title": "A", "source": "hn", "id": 1},
                              {"title": "B", "source": "hn", "id": 2}], "degraded": False}

        orig = collect_source.run
        collect_source.run = fake_run
        try:
            seen_items.filter_fresh([{"title": "A", "source": "hn", "id": 1}])  # "A" уже видели
            _, _, fresh_n, _ = harvest._source_signature()
            self.assertEqual(fresh_n, 1)  # только "B" свежий
            _, _, fresh_n2, _ = harvest._source_signature()
            self.assertEqual(fresh_n2, 1)  # повторный вызов — та же цифра (count_fresh не мутирует)
        finally:
            collect_source.run = orig
            seen_items.PATH = orig_path

    def test_status_from_out_per_source(self):
        # живой статус: считает items по источникам, помечает упавшие из partial_errors.
        # Источники берём из harvest.SOURCES (не хардкодим имена) — при 1 источнике тест
        # проверяет только "успешный" сценарий, при 3+ добавляются "упавший" и "молчащий".
        sources = list(harvest.SOURCES)
        ok_source = sources[0]
        rest = sources[1:]
        failed_source = rest[0] if rest else None

        items = [{"title": "a", "source": ok_source}, {"title": "b", "source": ok_source}]
        partial_errors = [f"{failed_source}: 403 Blocked"] if failed_source else []
        out = {"items": items, "degraded": False, "partial_errors": partial_errors}
        st = harvest._status_from_out(out)

        self.assertEqual(set(st["sources"]), set(sources))          # все источники представлены
        ok_entry = st["sources"][ok_source]
        self.assertEqual(ok_entry["items"], 2)
        self.assertTrue(ok_entry["ok"])
        self.assertIsNone(ok_entry["error"])
        self.assertEqual(ok_entry["beta"], ok_source not in harvest.USER_VERIFIED_SOURCES)

        if failed_source:
            self.assertFalse(st["sources"][failed_source]["ok"])    # в partial_errors -> упал
            self.assertIn("403", st["sources"][failed_source]["error"])
        for silent in rest[1:]:                                      # не дал items -> ok=False
            self.assertFalse(st["sources"][silent]["ok"])
        self.assertFalse(st["degraded"])

    def test_status_from_out_all_degraded(self):
        # все упали -> fallback без source-поля -> degraded, у всех ok=False
        out = {"items": [{"title": "fallback"}], "degraded": True, "degraded_reason": "net down"}
        st = harvest._status_from_out(out)
        self.assertTrue(st["degraded"])
        self.assertTrue(all(not v["ok"] for v in st["sources"].values()))

    def test_should_run_gate_vs_force(self):
        # гейт пропускает неизменную ленту в автоцикле, но force (ручной клик) его перепрыгивает
        tmp = tempfile.mkdtemp(prefix="harvest_sr_")
        orig = harvest.STATE_FILE
        harvest.STATE_FILE = os.path.join(tmp, "harvest_state.json")
        try:
            harvest._save_sig("SAME")
            self.assertFalse(harvest._should_run("SAME", force=False))  # не менялась, автоцикл → пропуск
            self.assertTrue(harvest._should_run("SAME", force=True))    # ручной клик → всё равно гоним
            self.assertTrue(harvest._should_run("DIFF", force=False))   # лента изменилась → гоним
            self.assertTrue(harvest._should_run(None, force=False))     # отпечаток не снят → гоним
        finally:
            harvest.STATE_FILE = orig

    def test_should_run_gate_zero_fresh_items_skips_even_if_hash_changed(self):
        # лента "изменилась" (другой хеш), но fresh_n==0 -> всё это старьё, точный пропуск
        tmp = tempfile.mkdtemp(prefix="harvest_sr2_")
        orig = harvest.STATE_FILE
        harvest.STATE_FILE = os.path.join(tmp, "harvest_state.json")
        try:
            harvest._save_sig("OLD")
            self.assertFalse(harvest._should_run("NEW", force=False, fresh_n=0))
            self.assertTrue(harvest._should_run("NEW", force=False, fresh_n=1))   # есть 1 свежий -> гоним
            self.assertTrue(harvest._should_run("NEW", force=True, fresh_n=0))    # force всё равно гонит
        finally:
            harvest.STATE_FILE = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
