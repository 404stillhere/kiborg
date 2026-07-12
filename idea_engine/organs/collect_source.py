"""Орган: collect_source — тянет свежие ВНЕШНИЕ items (сырьё для идей).

Контракт органа: run(inputs, env) -> dict. Внутри run() ноль глобальных обращений:
источник(и), лимит, таймаут приходят через env; ключей орган не берёт.

Источники — все публичные API/страницы, БЕЗ ключа: Hacker News, Reddit (r/SideProject),
Lobsters, GitHub Trending (HTML-скрейп, официального API нет). Плюс один КЛЮЧЕВОЙ источник —
Telegram-каналы ("telegram", см. _telegram) — читается через личный ТГ-аккаунт (pyrogram),
а не публичный API, поэтому единственный требует env["telegram_channels"] + креды.
env["source"] — один источник, env["sources"] — список (тогда бюджет env["n"] делится
между ними и сырьё СМЕШИВАЕТСЯ в одном ответе — межисточниковые дубли режет downstream-
дедуп в harvest). Неизвестный источник или сетевой сбой -> честный fallback на встроенный
сэмпл, degraded=True. Один источник упал, но другие дали сырьё -> НЕ degraded (сырьё есть),
но ошибка видна в partial_errors — для диагностики, без блокировки органа.
"""
import json
import os
import random
import re
import subprocess
import urllib.request

HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
REDDIT_TOP = "https://www.reddit.com/r/SideProject/top.json?t=day&limit={}"
LOBSTERS_HOT = "https://lobste.rs/hottest.json"
GH_TRENDING = "https://github.com/trending"
_UA = "kiborg-idea-engine/1.0 (personal script, non-commercial)"

_FALLBACK = [
    {"title": "Show HN: local-first sync engine in Rust", "url": "", "id": 0},
    {"title": "Ask HN: how do you run agents unattended?", "url": "", "id": 0},
    {"title": "A tiny CRDT in 200 lines", "url": "", "id": 0},
    {"title": "Building a personal search engine over your notes", "url": "", "id": 0},
]


def _get(url_or_req, timeout):
    with urllib.request.urlopen(url_or_req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _hn(n, timeout, env):
    ids = _get(HN_TOP, timeout)[:n]
    items = []
    for i in ids:
        it = _get(HN_ITEM.format(i), timeout)
        if it and it.get("title"):
            items.append({"title": it["title"], "url": it.get("url", ""), "id": it.get("id")})
    if not items:
        raise ValueError("hn returned empty")
    return items


def _reddit(n, timeout, env):
    # без User-Agent reddit отвечает 429 — ставим свой (публичный .json-эндпоинт, без ключа)
    req = urllib.request.Request(REDDIT_TOP.format(n), headers={"User-Agent": _UA})
    data = _get(req, timeout)
    items = []
    for c in (data.get("data", {}).get("children") or [])[:n]:
        d = c.get("data", {})
        title = d.get("title")
        if title:
            url = d.get("url") or ("https://reddit.com" + d.get("permalink", ""))
            items.append({"title": title, "url": url, "id": d.get("id")})
    if not items:
        raise ValueError("reddit returned empty")
    return items


def _lobsters(n, timeout, env):
    data = _get(LOBSTERS_HOT, timeout)
    items = []
    for it in (data or [])[:n]:
        title = it.get("title")
        if title:
            items.append({"title": title, "url": it.get("url") or it.get("comments_url", ""),
                          "id": it.get("short_id")})
    if not items:
        raise ValueError("lobsters returned empty")
    return items


def _gh_trending(n, timeout, env):
    # официального API нет -> HTML-скрейп; парсим ТЕРПИМО (только class~lh-condensed + href
    # owner/repo), любая непонятная разметка -> ValueError -> честный degrade, не краш.
    req = urllib.request.Request(GH_TRENDING, headers={"User-Agent": "Mozilla/5.0 (" + _UA + ")"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", errors="replace")
    blocks = re.findall(r'<h2[^>]*class="[^"]*lh-condensed[^"]*"[^>]*>(.*?)</h2>', html, re.DOTALL)
    items = []
    for b in blocks[:n]:
        m = re.search(r'href="/([^"/?]+)/([^"/?]+)"', b)
        if m:
            owner, repo = m.group(1), m.group(2)
            items.append({"title": f"{owner}/{repo}", "url": f"https://github.com/{owner}/{repo}",
                          "id": f"{owner}/{repo}"})  # repo сам по себе стабильный id (для дедупа items)
    if not items:
        raise ValueError("gh_trending: no repos parsed")
    return items


# Telegram — единственный КЛЮЧЕВОЙ источник: читает через pyrogram (личный ТГ-аккаунт), а не
# публичный API. pyrogram — НЕ stdlib, поэтому вызываем его отдельным процессом на venv darbot
# (там уже стоит) в режиме --rpc; сама collect_source.py stdlib-only остаётся (только subprocess).
# Вендоренный орган: cyborg/organs_vendored/collect_tg_news.py (копия darbot/organ.py, EXTRACT_ORGAN).
_TG_PYTHON_DEFAULT = "M:/projects/darbot/venv/Scripts/python.exe"
_TG_RUNNER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "cyborg", "organs_vendored", "collect_tg_news.py",
)


def _telegram(n, timeout, env):
    channels = list(env.get("telegram_channels") or [])
    if not channels:
        raise ValueError("telegram: no channels configured (env['telegram_channels'])")
    api_id = env.get("telegram_api_id")
    api_hash = env.get("telegram_api_hash")
    session = env.get("telegram_session")
    if not (api_id and api_hash and session):
        raise ValueError("telegram: missing creds (telegram_api_id/telegram_api_hash/telegram_session)")
    python_exe = env.get("telegram_python", _TG_PYTHON_DEFAULT)
    # Список каналов может быть заметно шире бюджета n (напр. 21 канал, n=6) — тянуть историю
    # у ВСЕХ на 1 пост каждый и потом обрубать items[:n] систематически обделило бы "хвост"
    # списка (никогда бы не попадал в первые n). Вместо этого — случайная выборка ДО фетча:
    # ограничивает и число pyrogram-вызовов (не долбим все 21 каждый прогон), и даёт каналам
    # из хвоста шанс попасть в выдачу на следующих прогонах (ротация по времени, не по позиции).
    sample_size = min(len(channels), max(1, n))
    channels = random.sample(channels, sample_size) if len(channels) > sample_size else channels
    limit_per_channel = max(1, n // max(1, len(channels)))  # n — общий бюджет ИСТОЧНИКА, не на канал

    payload = json.dumps({
        "inputs": {"channels": list(channels), "limit_per_channel": limit_per_channel},
        "env": {"TELEGRAM_API_ID": api_id, "TELEGRAM_API_HASH": api_hash, "TELEGRAM_SESSION": session},
    }).encode("utf-8")
    # timeout шире, чем у HTTP-источников: логин pyrogram-клиента + запуск отдельного питона —
    # дороже одного GET; env["telegram_timeout"] можно поднять отдельно, не трогая общий timeout.
    tg_timeout = float(env.get("telegram_timeout", max(timeout, 25)))
    proc = subprocess.run([python_exe, _TG_RUNNER, "--rpc"], input=payload,
                           capture_output=True, timeout=tg_timeout)
    if proc.returncode != 0:
        raise ValueError(f"telegram: rpc exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    result = json.loads(proc.stdout.decode("utf-8"))

    items = []
    for it in result.get("items", []):
        title = (it.get("text") or "").strip().splitlines()[0][:200] if it.get("text") else ""
        if title:
            items.append({"title": title, "url": it.get("url") or "",
                          "id": f"{it.get('channel')}:{it.get('id')}"})
    if not items:
        reason = "; ".join(result.get("warnings", [])) or "no posts"
        raise ValueError(f"telegram returned empty: {reason}")
    return items[:n]


_SOURCES = {
    "hn": _hn,
    "reddit": _reddit,
    "lobsters": _lobsters,
    "gh_trending": _gh_trending,
    "telegram": _telegram,
}


def run(inputs, env):
    env = env or {}
    n = int(env.get("n", 8))
    timeout = float(env.get("timeout", 8))
    sources = env.get("sources")
    names = list(sources) if sources else [env.get("source", "hn")]
    per_n = max(1, -(-n // len(names)))  # общий бюджет n делим (ceil) между источниками

    items, errors = [], []
    for name in names:
        fn = _SOURCES.get(name)
        if fn is None:
            errors.append(f"{name}: unknown source")
            continue
        try:
            got = fn(per_n, timeout, env)
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        for it in got:
            it = dict(it)
            it.setdefault("source", name)
            items.append(it)

    label = "+".join(names)
    if not items:
        # ни один источник не дал сырья -> честный резерв. degrade — это УСПЕХ с резервом,
        # а НЕ краш: НЕ ключ "error" (иначе киборг зря пометит источник сдохшим и заблокирует).
        return {"items": list(_FALLBACK[:n]), "source": label, "degraded": True,
                "degraded_reason": "; ".join(errors) or "no items"}

    out = {"items": items, "source": label, "degraded": False}
    if errors:
        out["partial_errors"] = errors  # часть источников не ответила, но сырьё уже есть
    return out


if __name__ == "__main__":
    print(json.dumps(run({}, {"n": 5}), ensure_ascii=False, indent=2))
