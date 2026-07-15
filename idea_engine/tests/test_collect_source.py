"""Тест collect_source: честный degrade без ложного ключа 'error'.

При обрыве сети орган отдаёт фолбэк-сэмпл и помечает degraded=True + degraded_reason — но НЕ
'error' (ключ 'error' в контракте киборга = «орган упал, переизбрать/заблокировать»; здесь орган
УСПЕШНО отдал сырьё через резерв, блокировать его нельзя).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import collect_source  # noqa: E402


class TestCollectSource(unittest.TestCase):
    def setUp(self):
        self._orig = collect_source.urllib.request.urlopen

    def tearDown(self):
        collect_source.urllib.request.urlopen = self._orig

    def test_degrade_on_network_fail_no_false_error(self):
        def boom(*a, **k):
            raise OSError("network down")
        collect_source.urllib.request.urlopen = boom
        out = collect_source.run({}, {"n": 4, "source": "hn"})
        self.assertTrue(out["items"])                 # резерв отдан
        self.assertTrue(out["degraded"])
        self.assertIn("degraded_reason", out)          # причина сохранена для диагностики
        self.assertNotIn("error", out)                 # но НЕ ложный 'error' (иначе киборг зря блокирует)

    def test_unknown_source_degraded_no_error(self):
        out = collect_source.run({}, {"n": 3, "source": "unknown"})
        self.assertTrue(out["degraded"])
        self.assertNotIn("error", out)


class TestNewSources(unittest.TestCase):
    """reddit/lobsters/gh_trending — каждый мокается на СВОЁМ формате ответа сети."""

    def setUp(self):
        self._orig_get = collect_source._get
        self._orig_urlopen = collect_source.urllib.request.urlopen

    def tearDown(self):
        collect_source._get = self._orig_get
        collect_source.urllib.request.urlopen = self._orig_urlopen

    def test_hn_happy_path_parses_topstories(self):
        # _hn: GET topstories -> список id -> GET item по каждому -> {title,url,id}. Пост без
        # title отбрасывается. (Раньше HN был покрыт ТОЛЬКО degrade-веткой, счастливый путь — нет.)
        def fake_get(url_or_req, timeout):
            url = url_or_req if isinstance(url_or_req, str) else url_or_req.full_url
            if "topstories" in url:
                return [101, 102, 103]
            iid = int(url.rsplit("/", 1)[1].split(".")[0])   # .../item/<id>.json
            titles = {101: "Show HN: local-first thing", 102: "Ask HN: unattended agents", 103: ""}
            return {"id": iid, "title": titles.get(iid, ""), "url": f"https://h/{iid}"}
        collect_source._get = fake_get
        out = collect_source.run({}, {"n": 3, "source": "hn"})
        self.assertFalse(out["degraded"])
        titles = [it["title"] for it in out["items"]]
        self.assertIn("Show HN: local-first thing", titles)
        self.assertNotIn("", titles)                          # пост без title отброшен
        self.assertEqual(len(out["items"]), 2)                # 3 id, один без title
        self.assertTrue(all(it["source"] == "hn" for it in out["items"]))
        self.assertEqual(out["items"][0]["id"], 101)          # id проброшен из item

    def test_reddit_parses_children(self):
        def fake_get(url_or_req, timeout):
            return {"data": {"children": [
                {"data": {"title": "Show r/SideProject: my tiny app", "url": "https://x.com/1", "id": "abc"}},
                {"data": {"title": "", "url": "https://x.com/2", "id": "def"}},  # без title -> пропуск
            ]}}
        collect_source._get = fake_get
        out = collect_source.run({}, {"n": 5, "source": "reddit"})
        self.assertFalse(out["degraded"])
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["title"], "Show r/SideProject: my tiny app")
        self.assertEqual(out["items"][0]["source"], "reddit")

    def test_lobsters_parses_list(self):
        def fake_get(url_or_req, timeout):
            return [{"title": "A neat CLI trick", "url": "https://l/1", "short_id": "aa"}]
        collect_source._get = fake_get
        out = collect_source.run({}, {"n": 5, "source": "lobsters"})
        self.assertFalse(out["degraded"])
        self.assertEqual(out["items"][0]["title"], "A neat CLI trick")

    def test_gh_trending_parses_html(self):
        html = (
            '<article><h2 class="h3 lh-condensed">\n'
            '<a href="/octocat/hello-world">\n  octocat /\n  hello-world\n</a>\n'
            '</h2></article>'
        )

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return html.encode("utf-8")

        collect_source.urllib.request.urlopen = lambda req, timeout: _Resp()
        out = collect_source.run({}, {"n": 5, "source": "gh_trending"})
        self.assertFalse(out["degraded"])
        self.assertEqual(out["items"][0]["title"], "octocat/hello-world")

    def test_gh_trending_bad_html_degrades_not_crashes(self):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"<html>no repos here</html>"

        collect_source.urllib.request.urlopen = lambda req, timeout: _Resp()
        out = collect_source.run({}, {"n": 5, "source": "gh_trending"})
        self.assertTrue(out["degraded"])
        self.assertNotIn("error", out)

    def test_merged_sources_split_budget_and_tag_origin(self):
        def fake_get(url_or_req, timeout):
            url = url_or_req if isinstance(url_or_req, str) else url_or_req.full_url
            if "reddit" in url:
                return {"data": {"children": [{"data": {"title": "R idea", "id": "r1"}}]}}
            if "lobste" in url:
                return [{"title": "L idea", "short_id": "l1"}]
            return []
        collect_source._get = fake_get
        out = collect_source.run({}, {"n": 10, "sources": ["reddit", "lobsters"]})
        self.assertFalse(out["degraded"])
        titles = {it["title"]: it["source"] for it in out["items"]}
        self.assertEqual(titles["R idea"], "reddit")
        self.assertEqual(titles["L idea"], "lobsters")
        self.assertEqual(out["source"], "reddit+lobsters")

    def test_one_source_down_others_ok_not_degraded_but_reports_partial(self):
        def fake_get(url_or_req, timeout):
            url = url_or_req if isinstance(url_or_req, str) else url_or_req.full_url
            if "lobste" in url:
                raise OSError("lobsters down")
            return {"data": {"children": [{"data": {"title": "R idea", "id": "r1"}}]}}
        collect_source._get = fake_get
        out = collect_source.run({}, {"n": 10, "sources": ["reddit", "lobsters"]})
        self.assertFalse(out["degraded"])          # сырьё есть -> не degraded
        self.assertIn("partial_errors", out)
        self.assertTrue(any("lobsters" in e for e in out["partial_errors"]))

    def test_all_sources_down_degrades_with_fallback(self):
        def boom(*a, **k):
            raise OSError("network down")
        collect_source._get = boom
        out = collect_source.run({}, {"n": 4, "sources": ["reddit", "lobsters"]})
        self.assertTrue(out["items"])
        self.assertTrue(out["degraded"])
        self.assertIn("degraded_reason", out)
        self.assertNotIn("error", out)


class _FakeProc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestTelegramSource(unittest.TestCase):
    """telegram — единственный keyed источник: subprocess на venv darbot, --rpc, JSON stdin/stdout.
    subprocess.run мокается напрямую (аналог мока urlopen для HTTP-источников выше) — без сети,
    без pyrogram, без реальной сессии."""

    def setUp(self):
        self._orig_run = collect_source.subprocess.run

    def tearDown(self):
        collect_source.subprocess.run = self._orig_run

    def _creds_env(self, extra=None):
        env = {"n": 4, "source": "telegram", "telegram_channels": ["@a"],
               "telegram_api_id": "1", "telegram_api_hash": "h", "telegram_session": "s"}
        if extra:
            env.update(extra)
        return env

    def test_no_channels_configured_degrades_no_crash(self):
        # ни креды, ни каналы не заданы (напр. вызов _source_signature без telegram-env) -> честный degrade
        out = collect_source.run({}, {"n": 4, "source": "telegram"})
        self.assertTrue(out["degraded"])
        self.assertNotIn("error", out)
        self.assertIn("no channels", out["degraded_reason"])

    def test_missing_creds_degrades_no_crash(self):
        out = collect_source.run({}, {"n": 4, "source": "telegram", "telegram_channels": ["@a"]})
        self.assertTrue(out["degraded"])
        self.assertIn("missing creds", out["degraded_reason"])

    def test_parses_subprocess_json_into_items(self):
        def fake_run(cmd, input, capture_output, timeout):  # noqa: A002
            payload = {"items": [{"channel": "@a", "id": 5, "text": "Идея из ТГ\nвторая строка",
                                   "url": "https://t.me/a/5"}], "warnings": []}
            return _FakeProc(0, json.dumps(payload).encode("utf-8"))
        collect_source.subprocess.run = fake_run
        out = collect_source.run({}, self._creds_env())
        self.assertFalse(out["degraded"])
        self.assertEqual(out["items"][0]["title"], "Идея из ТГ")  # только первая строка поста
        self.assertEqual(out["items"][0]["id"], "@a:5")
        self.assertEqual(out["items"][0]["source"], "telegram")

    def test_rpc_nonzero_exit_captured_as_partial_error_not_crash(self):
        def fake_run(cmd, input, capture_output, timeout):  # noqa: A002
            return _FakeProc(1, b"", b"traceback: boom")
        collect_source.subprocess.run = fake_run
        out = collect_source.run({}, self._creds_env())
        self.assertTrue(out["degraded"])
        self.assertIn("rpc exit 1", out["degraded_reason"])

    def test_all_channels_unresolved_degrades(self):
        def fake_run(cmd, input, capture_output, timeout):  # noqa: A002
            payload = {"items": [], "warnings": ["@a: Username not found: a"]}
            return _FakeProc(0, json.dumps(payload).encode("utf-8"))
        collect_source.subprocess.run = fake_run
        out = collect_source.run({}, self._creds_env())
        self.assertTrue(out["degraded"])
        self.assertIn("Username not found", out["degraded_reason"])

    def test_merges_with_keyless_sources(self):
        orig_get = collect_source._get

        def fake_get(url_or_req, timeout):
            return {"data": {"children": [{"data": {"title": "R idea", "id": "r1"}}]}}

        def fake_run(cmd, input, capture_output, timeout):  # noqa: A002
            payload = {"items": [{"channel": "@a", "id": 1, "text": "TG idea"}], "warnings": []}
            return _FakeProc(0, json.dumps(payload).encode("utf-8"))

        collect_source._get = fake_get
        collect_source.subprocess.run = fake_run
        try:
            out = collect_source.run({}, self._creds_env({"sources": ["reddit", "telegram"]}))
        finally:
            collect_source._get = orig_get
        self.assertFalse(out["degraded"])
        sources_seen = {it["source"] for it in out["items"]}
        self.assertEqual(sources_seen, {"reddit", "telegram"})

    def test_payload_limit_per_channel_split(self):
        # внутрянка _telegram: бюджет n делится на число каналов -> limit_per_channel = max(1, n//k).
        # Раньше payload к subprocess не проверялся вообще (мок только отдавал stdout).
        captured = {}

        def fake_run(cmd, input, capture_output, timeout):  # noqa: A002
            captured["payload"] = json.loads(input.decode("utf-8"))
            return _FakeProc(0, json.dumps(
                {"items": [{"channel": "@a", "id": 1, "text": "t"}], "warnings": []}).encode("utf-8"))
        collect_source.subprocess.run = fake_run
        collect_source.run({}, self._creds_env({"telegram_channels": ["@a", "@b"], "n": 4}))
        inp = captured["payload"]["inputs"]
        self.assertEqual(inp["limit_per_channel"], 2)          # max(1, 4//2)
        self.assertEqual(set(inp["channels"]), {"@a", "@b"})   # каналов <= бюджета -> все идут

    def test_channels_sampled_down_to_budget(self):
        # каналов больше бюджета n -> случайная выборка ДО фетча ограничивает число каналов до n
        # (чтоб не долбить все каждый прогон и дать хвосту списка шанс). Ассертим ЧИСЛО, не какие.
        captured = {}

        def fake_run(cmd, input, capture_output, timeout):  # noqa: A002
            captured["payload"] = json.loads(input.decode("utf-8"))
            return _FakeProc(0, json.dumps(
                {"items": [{"channel": "@x", "id": 1, "text": "t"}], "warnings": []}).encode("utf-8"))
        collect_source.subprocess.run = fake_run
        pool = ["@a", "@b", "@c", "@d", "@e"]
        collect_source.run({}, self._creds_env({"telegram_channels": pool, "n": 2}))
        inp = captured["payload"]["inputs"]
        self.assertEqual(len(inp["channels"]), 2)              # 5 каналов -> выборка до n=2
        self.assertTrue(set(inp["channels"]).issubset(set(pool)))
        self.assertEqual(inp["limit_per_channel"], 1)          # max(1, 2//2)


class TestBudgetSplit(unittest.TestCase):
    """per_n: общий бюджет n делится (ceil) между источниками и РЕАЛЬНО доходит до каждого fn.
    Раньше слияние проверяло тег origin, но не сам разбитый бюджет."""

    def setUp(self):
        self._orig_sources = dict(collect_source._SOURCES)

    def tearDown(self):
        collect_source._SOURCES.clear()
        collect_source._SOURCES.update(self._orig_sources)

    def test_per_n_ceil_split_reaches_each_source(self):
        seen = []

        def spy(n, timeout, env):
            seen.append(n)
            return [{"title": "x", "url": "", "id": "1"}]
        collect_source._SOURCES["reddit"] = spy
        collect_source._SOURCES["lobsters"] = spy
        collect_source.run({}, {"n": 7, "sources": ["reddit", "lobsters"]})
        self.assertEqual(seen, [4, 4])            # ceil(7/2)=4 каждому (не floor=3)

    def test_per_n_single_source_gets_full_budget(self):
        seen = []

        def spy(n, timeout, env):
            seen.append(n)
            return [{"title": "x", "url": "", "id": "1"}]
        collect_source._SOURCES["reddit"] = spy
        collect_source.run({}, {"n": 5, "source": "reddit"})
        self.assertEqual(seen, [5])               # один источник -> весь бюджет


class _TmpDirTest(unittest.TestCase):
    """База для тестов с реальной временной папкой: setUp/tearDown/_write — общий каркас, чтобы
    не дублировать его в каждом классе (TestFilesSource, TestProbePaths). Префикс — свой у каждого."""
    _PREFIX = "kiborg_tmp_"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix=self._PREFIX)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p) or self.tmp, exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(p, mode, **({} if mode == "wb" else {"encoding": "utf-8"})) as f:
            f.write(content)
        return p


class TestFilesSource(_TmpDirTest):
    """Источник «files»: читает текстовые файлы из папок как сырьё, пропускает секреты/мусор/
    бинарь/крупняк. Реальная ФС во временной папке (не сеть) — как настоящий обход."""
    _PREFIX = "kiborg_files_"

    def test_no_folders_configured_degrades_no_crash(self):
        out = collect_source.run({}, {"n": 4, "source": "files"})
        self.assertTrue(out["degraded"])
        self.assertNotIn("error", out)                     # честный degrade, не блокировка органа
        self.assertIn("no folders", out["degraded_reason"])

    def test_reads_text_files_as_headlines(self):
        self._write("store.py", '"""Хранилище идей — дедуп и кэп."""\nimport os\n')
        self._write("README.md", "# Киборг\nгенератор идей\n")
        out = collect_source.run({}, {"n": 10, "source": "files", "files_paths": [self.tmp]})
        self.assertFalse(out["degraded"])
        titles = [it["title"] for it in out["items"]]
        self.assertTrue(any("store.py" in t and "Хранилище идей" in t for t in titles))
        self.assertTrue(any("README.md" in t and "Киборг" in t for t in titles))
        self.assertTrue(all(it["source"] == "files" for it in out["items"]))

    def test_skips_secret_files(self):
        self._write("llm_keys.env", "OPENAI_KEY=sk-secret\n")
        self._write("kiborg_tg.session", "session-bytes\n")
        self._write("my_token.txt", "ghp_supersecret\n")
        self._write("app.py", '"""реальный код."""\n')
        out = collect_source.run({}, {"n": 10, "source": "files", "files_paths": [self.tmp]})
        titles = " ".join(it["title"] for it in out["items"]).lower()
        self.assertIn("app.py", titles)
        self.assertNotIn(".env", titles)                   # секрет по расширению — не прочитан
        self.assertNotIn(".session", titles)
        self.assertNotIn("token", titles)                  # секрет по подстроке имени

    def test_skips_junk_dirs_and_binaries(self):
        self._write("src/main.py", '"""главный модуль."""\n')
        self._write("node_modules/lib/index.js", "// dep\n")
        self._write("__pycache__/main.cpython-312.pyc", b"\x00\x01bin")
        self._write("logo.png", b"\x89PNG\r\n")
        out = collect_source.run({}, {"n": 10, "source": "files", "files_paths": [self.tmp]})
        titles = " ".join(it["title"] for it in out["items"])
        self.assertIn("main.py", titles)
        self.assertNotIn("node_modules", titles)           # мусорная папка отсечена
        self.assertNotIn(".pyc", titles)
        self.assertNotIn(".png", titles)                   # бинарь по расширению

    def test_oversized_file_skipped(self):
        self._write("big.md", "# huge\n" + ("x" * (300 * 1024)))
        self._write("small.md", "# small doc\nтекст\n")
        out = collect_source.run({}, {"n": 10, "source": "files", "files_paths": [self.tmp]})
        titles = " ".join(it["title"] for it in out["items"])
        self.assertIn("small.md", titles)
        self.assertNotIn("big.md", titles)                 # больше _FILES_MAX_BYTES — мимо

    def test_empty_folder_degrades_no_error(self):
        self._write("photo.jpg", b"\xff\xd8bin")            # папка есть, текстовых файлов нет
        out = collect_source.run({}, {"n": 4, "source": "files", "files_paths": [self.tmp]})
        self.assertTrue(out["degraded"])
        self.assertNotIn("error", out)

    def test_headline_strips_wrappers_and_skips_technical(self):
        p = self._write("m.py", "#!/usr/bin/env python\nimport os\n# настоящий смысл файла\ncode\n")
        self.assertEqual(collect_source._files_headline(p), "настоящий смысл файла")

    def test_id_is_abspath_for_dedup(self):
        self._write("a.py", '"""a."""\n')
        out = collect_source.run({}, {"n": 4, "source": "files", "files_paths": [self.tmp]})
        self.assertTrue(all(os.path.isabs(it["id"]) for it in out["items"]))

    def test_single_file_path_allowed(self):
        p = self._write("solo.md", "# соло\nтекст\n")
        out = collect_source.run({}, {"n": 4, "source": "files", "files_paths": [p]})
        self.assertFalse(out["degraded"])
        self.assertEqual(len(out["items"]), 1)
        self.assertIn("solo.md", out["items"][0]["title"])

    def test_sampled_down_to_budget(self):
        for i in range(8):
            self._write(f"f{i}.py", f'"""файл {i}."""\n')
        out = collect_source.run({}, {"n": 3, "source": "files", "files_paths": [self.tmp]})
        self.assertFalse(out["degraded"])                  # источник жив (не фолбэк-заглушка)
        self.assertEqual(len(out["items"]), 3)             # 8 файлов -> ровно бюджет n=3

    def test_merges_with_keyless_sources(self):
        self._write("x.py", '"""икс."""\n')
        orig_get = collect_source._get

        def fake_get(url_or_req, timeout):
            return {"data": {"children": [{"data": {"title": "R idea", "id": "r1"}}]}}
        collect_source._get = fake_get
        try:
            out = collect_source.run({}, {"n": 10, "sources": ["reddit", "files"],
                                          "files_paths": [self.tmp]})
        finally:
            collect_source._get = orig_get
        self.assertFalse(out["degraded"])
        self.assertEqual({it["source"] for it in out["items"]}, {"reddit", "files"})

    def test_secret_in_content_not_leaked_to_title(self):
        # ГЛАВНОЕ: секрет в СОДЕРЖИМОМ файла с ОБЫЧНЫМ именем не должен попасть в заголовок —
        # заголовок уходит в промпт LLM (ideate) ДО scrub_secrets. Имя-фильтр тут не спасает.
        # Секрет-ФОРМЫ собираем из кусков в РАНТАЙМЕ (не литералом): иначе в закоммиченном файле
        # лежала бы строка-как-секрет (sk-proj-…, tg-токен) → push-protection GitHub / скан выката
        # спотыкались бы о фикстуру и могли ЗАБЛОКИРОВАТЬ push. По кускам ни одна форма не целая.
        openai = "sk-" + "proj-" + "FAKE" + "0123456789abcdefghij"        # форма OpenAI-ключа
        tgtok = "1234567890" + ":" + "AAH" + "FAKEtoken" + "ABCDEFGHIJKLMNOPQRSTUVWX"  # форма tg-токена
        awssec = "wJalr" + "FAKE" + "EXAMPLE" + "KEY0123456789ABCDEF"      # значение AWS-секрета
        dbpass = "fakepass"
        self._write("cfg.py", f'API_KEY = "{openai}"\n"""нормальный докстринг."""\n')
        self._write("bot.py", f'TOKEN = "{tgtok}"\n')
        self._write("deploy.sh", f"#!/bin/sh\nexport AWS_SECRET_ACCESS_KEY={awssec}\n")
        self._write("db.py", f'DATABASE_URL = "postgres://user:{dbpass}@host/db"\n')
        out = collect_source.run({}, {"n": 10, "source": "files", "files_paths": [self.tmp]})
        blob = " ".join(it["title"] for it in out["items"])
        for secret in [openai, tgtok, awssec, dbpass]:
            self.assertNotIn(secret, blob)                 # ни одна собранная форма не утекла в заголовок
        # при этом секрет-строку сменяет следующая чистая строка (докстринг), файл не потерян
        self.assertTrue(any("cfg.py" in it["title"] and "докстринг" in it["title"] for it in out["items"]))

    def test_headline_keeps_coding_word_heading(self):
        # «# Coding standards» — обычный заголовок, НЕ PEP-263 объявление кодировки: не срезаем
        p = self._write("doc.md", "# Coding standards\nтекст\n")
        self.assertEqual(collect_source._files_headline(p), "Coding standards")

    def test_headline_skips_pep263_coding(self):
        p = self._write("m2.py", "# -*- coding: utf-8 -*-\n# настоящий смысл\nx=1\n")
        self.assertEqual(collect_source._files_headline(p), "настоящий смысл")

    def test_headline_handles_utf8_bom(self):
        p = os.path.join(self.tmp, "bom.md")
        with open(p, "wb") as f:
            f.write("﻿# Заголовок с BOM\nтекст\n".encode("utf-8"))
        self.assertEqual(collect_source._files_headline(p), "Заголовок с BOM")

    def test_walk_bounded_by_scan_cap(self):
        # предохранитель: гигантская/ошибочно заданная папка (диск-корень) не заставляет обойти
        # ВСЁ — потолок _FILES_MAX_SCAN обрывает обход, тик автосбора не виснет
        for i in range(50):
            self._write(f"f{i}.py", f'"""файл {i}."""\n')
        orig = collect_source._FILES_MAX_SCAN
        collect_source._FILES_MAX_SCAN = 10
        try:
            out = collect_source.run({}, {"n": 100, "source": "files", "files_paths": [self.tmp]})
            self.assertLessEqual(len(out["items"]), 10)     # осмотрено <=10 из 50 -> обход оборван
        finally:
            collect_source._FILES_MAX_SCAN = orig


class TestProbePaths(_TmpDirTest):
    """probe_paths — дешёвая проба папок для пульта: путь существует? сколько ПРИГОДНЫХ
    текстовых файлов? Тот же фильтр _files_is_candidate, что у реального сбора (одна правда)."""
    _PREFIX = "kiborg_probe_"

    def test_missing_path_marked_not_exists(self):
        res = collect_source.probe_paths([os.path.join(self.tmp, "нет-такой-папки")])
        (_path, info), = res.items()
        self.assertFalse(info["exists"])
        self.assertEqual(info["files"], 0)

    def test_counts_only_text_candidates(self):
        self._write("a.py", '"""a."""\n')
        self._write("b.md", "# b\n")
        self._write("logo.png", b"\x89PNG")               # бинарь — не в счёт
        self._write("llm_keys.env", "K=sk-x")             # секрет по расширению — не в счёт
        self._write("node_modules/x.js", "// dep\n")      # мусорная папка — не в счёт
        info = collect_source.probe_paths([self.tmp])[self.tmp]
        self.assertTrue(info["exists"])
        self.assertEqual(info["files"], 2)                # ровно a.py + b.md
        self.assertFalse(info["capped"])

    def test_count_matches_real_collect(self):
        # инвариант: сколько probe насчитал = сколько _files реально соберёт (общий фильтр)
        for i in range(5):
            self._write(f"f{i}.py", f'"""файл {i}."""\n')
        self._write("secret_token.txt", "ghp_x")          # оба (probe и сбор) должны пропустить
        probed = collect_source.probe_paths([self.tmp])[self.tmp]["files"]
        out = collect_source.run({}, {"n": 100, "source": "files", "files_paths": [self.tmp]})
        self.assertEqual(probed, len(out["items"]))

    def test_single_file_counts_one(self):
        p = self._write("solo.md", "# соло\n")
        info = collect_source.probe_paths([p])[p]
        self.assertTrue(info["exists"])
        self.assertEqual(info["files"], 1)

    def test_scan_cap_marks_capped(self):
        for i in range(50):
            self._write(f"f{i}.py", f'"""файл {i}."""\n')
        orig = collect_source._FILES_MAX_SCAN
        collect_source._FILES_MAX_SCAN = 10
        try:
            info = collect_source.probe_paths([self.tmp])[self.tmp]
            self.assertTrue(info["capped"])               # обход обрезан потолком — честно помечен
            self.assertLessEqual(info["files"], 10)
        finally:
            collect_source._FILES_MAX_SCAN = orig

    def test_blank_and_nonstr_entries_ignored(self):
        res = collect_source.probe_paths(["", "   ", None, 123])
        self.assertEqual(res, {})                          # мусорные записи молча пропущены, не крашат

    def test_mixed_existing_and_missing(self):
        self._write("a.py", '"""a."""\n')
        missing = os.path.join(self.tmp, "ghost")
        res = collect_source.probe_paths([self.tmp, missing])
        self.assertTrue(res[self.tmp]["exists"] and res[self.tmp]["files"] == 1)
        self.assertFalse(res[missing]["exists"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
