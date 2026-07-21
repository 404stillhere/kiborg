"""Тесты гейта «источник изменился?» в harvest — чтобы не гонять LLM впустую.

Фиксируем:
  1. Отпечаток заголовков: порядок НЕ важен, изменение состава — важно.
  2. Персист сигнатуры: _save_sig -> _last_sig возвращает то же (атомарно, во временную папку).
"""

import contextlib
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import harvest  # noqa: E402
import seen_items  # noqa: E402


@contextlib.contextmanager
def _patched_source(run=None, feeds=("hn", "reddit"), folders=()):
    """save/patch/restore harvest.feeds.enabled + harvest.folders.current (+ опц. collect_source.run),
    ГАРАНТИРУЯ restore в finally. Вынесено из повторов save/patch/finally в тестах ниже: убирает
    боилерплейт И защищает от ЗАБЫТОГО restore — тот класс бага дал 130с tg-замок (2026-07-15: мок
    feeds на telegram без восстановления → _collect_locked брал реальный замок). feeds по умолчанию
    НЕ-telegram (["hn","reddit"]), чтобы гейт-проба не тянула tg-сессию."""
    from organs import collect_source

    of, od, oc = harvest.feeds.enabled, harvest.folders.current, collect_source.run
    harvest.feeds.enabled = lambda: list(feeds)
    harvest.folders.current = lambda: list(folders)
    if run is not None:
        collect_source.run = run
    try:
        yield
    finally:
        harvest.feeds.enabled, harvest.folders.current, collect_source.run = of, od, oc


class TestHarvestGate(unittest.TestCase):
    def test_titles_sig_order_independent_change_sensitive(self):
        a = harvest._titles_sig(["Идея А", "Идея Б", "Идея В"])
        b = harvest._titles_sig(["Идея В", "Идея А", "Идея Б"])  # тот же набор, другой порядок
        self.assertEqual(a, b)  # порядок не важен
        c = harvest._titles_sig(["Идея А", "Идея Б", "Идея Г"])  # состав изменился
        self.assertNotEqual(a, c)  # изменение поймано

    def test_source_env_carries_direction(self):
        # активное направление подкладывается в env ОБЕИХ кнопок (через _source_env)
        orig = harvest.direction.current
        harvest.direction.current = lambda: "железки"
        try:
            self.assertEqual(harvest._source_env().get("direction"), "железки")
        finally:
            harvest.direction.current = orig

    def test_source_env_no_direction_when_empty(self):
        orig = harvest.direction.current
        harvest.direction.current = lambda: ""
        try:
            self.assertNotIn("direction", harvest._source_env())  # пусто -> ключа нет
        finally:
            harvest.direction.current = orig

    def test_source_env_carries_files_paths_when_folders_set(self):
        # заданы папки -> источник-файлы получает их через env ОБЕИХ кнопок (_source_env),
        # и 'files' попадает в список активных источников
        orig = harvest.folders.current
        harvest.folders.current = lambda: ["M:/projects/kiborg"]
        try:
            env = harvest._source_env()
            self.assertEqual(env.get("files_paths"), ["M:/projects/kiborg"])
            self.assertIn("files", env["sources"])
        finally:
            harvest.folders.current = orig

    def test_source_env_no_files_paths_when_folders_empty(self):
        orig = harvest.folders.current
        harvest.folders.current = lambda: []
        try:
            env = harvest._source_env()
            self.assertNotIn("files_paths", env)  # пусто -> источник выключен
            self.assertNotIn("files", env["sources"])  # и не значится активным
        finally:
            harvest.folders.current = orig

    def test_telegram_creds_only_when_telegram_active(self):
        # РЕГРЕССИЯ 2026-07-15: telegram-креды/сессию кладём в env ТОЛЬКО если 'telegram' в active.
        # Иначе _collect_locked брал tg-замок (130с таймаут) на files-only прогон → вис (жалоба юзера).
        # ON → креды есть; OFF → нет (даже когда креды/сессия физически доступны).
        of, od, oc = harvest.feeds.enabled, harvest.folders.current, harvest._load_darbot_tg_creds
        os_sess = harvest._KIBORG_TG_SESSION
        tmp = tempfile.NamedTemporaryFile(suffix=".session", delete=False)
        tmp.close()
        harvest._KIBORG_TG_SESSION = tmp.name  # сессия «существует»
        harvest._load_darbot_tg_creds = lambda: ("1", "h")  # креды доступны
        harvest.folders.current = lambda: []
        try:
            harvest.feeds.enabled = lambda: ["telegram"]
            self.assertTrue(harvest._source_env().get("telegram_session"))  # ON → креды в env
            harvest.feeds.enabled = lambda: ["hn"]
            self.assertIsNone(harvest._source_env().get("telegram_session"))  # OFF → нет (замок не берётся)
        finally:
            harvest.feeds.enabled, harvest.folders.current, harvest._load_darbot_tg_creds = of, od, oc
            harvest._KIBORG_TG_SESSION = os_sess
            os.remove(tmp.name)

    def test_atomic_write_no_temp_and_content(self):
        # атомарная запись (переехала из stash в harvest): пишет содержимое, не оставляет .tmp,
        # перезапись поверх работает. Ею harvest пишет статус источников и отпечаток.
        tmp = tempfile.mkdtemp(prefix="harvest_aw_")
        path = os.path.join(tmp, "sub", "f.json")  # несуществующая подпапка — создаётся
        harvest._atomic_write(path, '{"a":1}')
        with open(path, encoding="utf-8") as f:  # with — не течёт хэндл (Windows: temp удалится)
            self.assertEqual(f.read(), '{"a":1}')
        harvest._atomic_write(path, '{"a":2}')  # перезапись поверх
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"a":2}')
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_sig_persist_roundtrip(self):
        tmp = tempfile.mkdtemp(prefix="harvest_")
        orig = harvest.STATE_FILE
        harvest.STATE_FILE = os.path.join(tmp, "harvest_state.json")
        try:
            self.assertIsNone(harvest._last_sig())  # пусто -> None
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
        # env харвеста несёт РОВНО активные источники (_active_sources: включённые ленты + files).
        # Мокаем feeds/folders на известный набор — тест детерминирован, не зависит от живого data/*.json
        # и не хардкодит имена дважды (проверяет ВЫВОД проводки, а не константу).
        with _patched_source(feeds=["telegram", "hn"]):
            env = harvest._harvest_env()
            self.assertEqual(env["sources"], ["telegram", "hn"])  # ровно включённые ленты (папок нет)
            self.assertEqual(env["sources"], harvest._active_sources())

    def test_harvest_env_requests_seen_items_filter(self):
        # трекер «уже видели» (по ID items) — включён ТОЛЬКО для харвеста, не для интерактива
        env = harvest._harvest_env()
        self.assertTrue(env["filter_seen_items"])

    def test_source_signature_uses_wide_n(self):
        # гейт «лента изменилась?» снимает отпечаток на ТОЙ ЖЕ ширине, что и прогон (без сети).
        # Мокаем feeds на НЕ-telegram ленты (["hn","reddit"]): telegram в active тянул бы в env
        # telegram_session → _collect_locked брал бы РЕАЛЬНЫЙ tg-замок (130с таймаут при контенции
        # с живым пультом) — тест висел 130с. Путь telegram покрыт test_source_env_carries_* и wiring.
        captured = {}

        def fake_run(inputs, env):
            captured.update(env)
            return {"items": [{"title": "A"}, {"title": "B"}], "degraded": False}

        with _patched_source(fake_run):
            sig, degraded, fresh_n, status, _out = harvest._source_signature()
        self.assertEqual(captured.get("n"), harvest.SOURCE_N)  # гейт и прогон смотрят одинаково глубоко
        self.assertEqual(captured.get("sources"), ["hn", "reddit"])  # и по тому же набору источников
        self.assertIsNotNone(sig)
        self.assertFalse(degraded)
        self.assertEqual(fresh_n, 2)  # items без id -> дедуп невозможен -> считаются свежими всегда
        self.assertEqual(set(status["sources"]), {"hn", "reddit"})  # статус покрывает активные источники

    def test_source_signature_covers_union_not_just_hn(self):
        # отпечаток должен реагировать на изменение в ЛЮБОМ источнике, не только HN — иначе
        # gate соврёт «не изменилось» при реальном churn в reddit/lobsters/gh_trending.
        calls = []

        def fake_run(inputs, env):
            calls.append(1)
            # первый вызов "старая" reddit-идея, второй — "новая" (HN-часть неизменна)
            title = "reddit idea v1" if len(calls) == 1 else "reddit idea v2"
            return {"items": [{"title": "hn idea"}, {"title": title}], "degraded": False}

        with _patched_source(fake_run):
            sig1, _, _, _, _ = harvest._source_signature()
            sig2, _, _, _, _ = harvest._source_signature()
        self.assertNotEqual(sig1, sig2)

    def test_source_signature_fresh_n_via_seen_items(self):
        # fresh_n считает по ID (не по тексту) и НЕ мутирует seen-файл (это только gate-проверка)
        orig_path = seen_items.PATH
        tmp = tempfile.mkdtemp(prefix="harvest_fresh_")
        seen_items.PATH = os.path.join(tmp, "seen_items.json")

        def fake_run(inputs, env):
            return {
                "items": [{"title": "A", "source": "hn", "id": 1}, {"title": "B", "source": "hn", "id": 2}],
                "degraded": False,
            }

        try:
            with _patched_source(fake_run):
                seen_items.filter_fresh([{"title": "A", "source": "hn", "id": 1}])  # "A" уже видели
                _, _, fresh_n, _, _ = harvest._source_signature()
                self.assertEqual(fresh_n, 1)  # только "B" свежий
                _, _, fresh_n2, _, _ = harvest._source_signature()
                self.assertEqual(fresh_n2, 1)  # повторный вызов — та же цифра (count_fresh не мутирует)
        finally:
            seen_items.PATH = orig_path

    def test_status_from_out_per_source(self):
        # живой статус: считает items по источникам, помечает упавшие из partial_errors.
        # Источники — активные (_active_sources), мокаем на 2 известных ленты: одна успешна,
        # вторая падает. Детерминировано, не зависит от живого data/*.json.
        with _patched_source(feeds=["telegram", "hn"]):
            sources = harvest._active_sources()  # ["telegram", "hn"]
            ok_source = sources[0]
            rest = sources[1:]
            failed_source = rest[0] if rest else None

            items = [{"title": "a", "source": ok_source}, {"title": "b", "source": ok_source}]
            partial_errors = [f"{failed_source}: 403 Blocked"] if failed_source else []
            out = {"items": items, "degraded": False, "partial_errors": partial_errors}
            st = harvest._status_from_out(out)

            self.assertEqual(set(st["sources"]), set(sources))  # все активные источники представлены
            ok_entry = st["sources"][ok_source]
            self.assertEqual(ok_entry["items"], 2)
            self.assertTrue(ok_entry["ok"])
            self.assertIsNone(ok_entry["error"])
            self.assertEqual(ok_entry["beta"], ok_source not in harvest.USER_VERIFIED_SOURCES)

            if failed_source:
                self.assertFalse(st["sources"][failed_source]["ok"])  # в partial_errors -> упал
                self.assertIn("403", st["sources"][failed_source]["error"])
        for silent in rest[1:]:  # не дал items -> ok=False
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
            self.assertTrue(harvest._should_run("SAME", force=True))  # ручной клик → всё равно гоним
            self.assertTrue(harvest._should_run("DIFF", force=False))  # лента изменилась → гоним
            self.assertTrue(harvest._should_run(None, force=False))  # отпечаток не снят → гоним
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
            self.assertTrue(harvest._should_run("NEW", force=False, fresh_n=1))  # есть 1 свежий -> гоним
            self.assertTrue(harvest._should_run("NEW", force=True, fresh_n=0))  # force всё равно гонит
        finally:
            harvest.STATE_FILE = orig


class TestDegradeNote(unittest.TestCase):
    """root #1: сигнал деградации виден в консоли/логе, а не спрятан за «доставлено N»."""

    def test_healthy_run_empty(self):
        self.assertEqual(harvest._degrade_note({}), "")
        self.assertEqual(harvest._degrade_note({"degraded": False, "dropped_stub": 0}), "")

    def test_degraded_source(self):
        self.assertEqual(harvest._degrade_note({"degraded": True}), "источник в фолбэке")

    def test_dropped_stub(self):
        self.assertEqual(harvest._degrade_note({"dropped_stub": 3}), "stub-отсеяно=3")

    def test_dropped_dup(self):
        # dropped_dup (незакоммиченная правка): идеи, отклонённые deliver как дубликаты,
        # рендерятся в человекочитаемый флаг — как соседние dropped_stub/degraded.
        self.assertEqual(harvest._degrade_note({"dropped_dup": 4}), "дубликатов=4")

    def test_redacted_secrets_flagged(self):
        # сигнал БЕЗОПАСНОСТИ (нашла фабрика б-3 2026-07-18): скраб вычистил секрет из идеи →
        # «секретов-вырезано=N» в флагах прогона, иначе счётчик redacted молча теряется.
        # Не деградация выдачи — но surface обязателен (в источник просочился секрет).
        self.assertEqual(harvest._degrade_note({"redacted": 2}), "секретов-вырезано=2")
        self.assertEqual(harvest._degrade_note({"redacted": 0}), "")  # чисто → нет флага

    def test_both_flags(self):
        note = harvest._degrade_note({"degraded": True, "dropped_stub": 2})
        self.assertIn("источник в фолбэке", note)
        self.assertIn("stub-отсеяно=2", note)

    def test_all_three_flags(self):
        # все три сигнала деградации в одной строке, разделены · (provider не передан → нет флага модели)
        note = harvest._degrade_note({"degraded": True, "dropped_stub": 2, "dropped_dup": 1})
        self.assertEqual(note, "источник в фолбэке · stub-отсеяно=2 · дубликатов=1")

    def test_provider_flagged_always(self):
        # реш. юзера 2026-07-21: провайдер генератора светится ВСЕГДА (id модели, что ответила),
        # без деления на «бесплатно/платно» — вся цепочка на closerouter, делить не на что.
        # Раньше (гибрид gemini→muse) gemini-подписку прятали, флажили только платный muse-фолбэк;
        # теперь тег «модель=…» показывает, какое плечо цепочки ответило (muse-spark=первичная,
        # deepseek/nemotron=фолбэк). Полезно для диагностики, не бюджет-деградация.
        self.assertEqual(harvest._degrade_note({"provider": "muse-spark"}), "модель=muse-spark")
        self.assertEqual(harvest._degrade_note({"provider": "deepseek"}), "модель=deepseek")
        self.assertEqual(harvest._degrade_note({"provider": "nemotron"}), "модель=nemotron")
        self.assertEqual(harvest._degrade_note({}), "")  # нет provider — нет флага

    def test_provider_with_other_flags(self):
        # модель встаёт в общую строку деградации рядом с источником/дубликатами
        note = harvest._degrade_note({"degraded": True, "provider": "deepseek", "dropped_dup": 1})
        self.assertEqual(note, "источник в фолбэке · дубликатов=1 · модель=deepseek")


class TestHarvestRunnerGracefulShutdown(unittest.TestCase):
    """KeyboardInterrupt в цикле harvest_runner.main обрабатывается корректно."""

    def test_keyboard_interrupt_exits_cleanly(self):
        """Ctrl+C (KeyboardInterrupt) в цикле прогона → возврат из main без traceback."""
        import harvest_runner

        # Мокаем Cyborg так, чтобы на 2-м прогоне поднял KeyboardInterrupt
        calls = []

        class FakeCyborg:
            def run(self, goal, env=None, on_step=None, on_progress=None):
                calls.append(len(calls))
                if len(calls) == 2:
                    raise KeyboardInterrupt()
                return {"result": 1, "dropped_stub": 0, "trace": []}

        # Подменяем Cyborg через мок (через patch-паттерн из test_wiring)
        orig_cyborg = harvest_runner.harvest.Cyborg
        harvest_runner.harvest.Cyborg = lambda *a, **kw: FakeCyborg()

        try:
            # main(argv) должен выйти без exception
            harvest_runner.main(["2"])  # 2 прогона, но 2-й прервётся
        except KeyboardInterrupt:
            self.fail("KeyboardInterrupt должен быть перехвачен внутри main")
        finally:
            harvest_runner.harvest.Cyborg = orig_cyborg

        # Первый прогон прошёл, второй — прерван (вызов run был 2 раза)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
