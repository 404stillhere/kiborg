"""Тест collect_source: честный degrade без ложного ключа 'error'.

При обрыве сети орган отдаёт фолбэк-сэмпл и помечает degraded=True + degraded_reason — но НЕ
'error' (ключ 'error' в контракте киборга = «орган упал, переизбрать/заблокировать»; здесь орган
УСПЕШНО отдал сырьё через резерв, блокировать его нельзя).
"""
import json
import os
import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
