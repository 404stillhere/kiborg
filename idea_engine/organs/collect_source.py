"""Орган: collect_source — тянет свежие ВНЕШНИЕ items (сырьё для идей).

Контракт органа: run(inputs, env) -> dict. Внутри run() ноль глобальных обращений:
источник(и), лимит, таймаут приходят через env; ключей орган не берёт.

Источники — все публичные API/страницы, БЕЗ ключа: Hacker News, Reddit (r/SideProject),
Lobsters, GitHub Trending (HTML-скрейп, официального API нет). Плюс один КЛЮЧЕВОЙ источник —
Telegram-каналы ("telegram", см. _telegram) — читается через личный ТГ-аккаунт (pyrogram),
а не публичный API, поэтому единственный требует env["telegram_channels"] + креды. И ЛОКАЛЬНЫЙ
источник "files" (см. _files) — читает текстовые файлы из папок env["files_paths"] как сырьё
(смотрит на них нейтрально, как на чужой проект; секреты и мусорные папки пропускает сам).
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


# ── Источник «files»: читает ТЕКСТОВЫЕ файлы из заданных папок как ещё одно сырьё для идей ──
# Смотрит на папку НЕЙТРАЛЬНО — как на чужой проект со стороны (не «свой код», без «чини себя»).
# Один файл = один «заголовок» {относительный_путь — первая содержательная строка}, дальше та
# же машина идей, что у лент. Настройки — env["files_paths"] (список папок и/или отдельных
# файлов); без них честный ValueError -> degrade. БЕЗОПАСНОСТЬ: секреты (*.env/*.session/ключи и
# имена с secret/token/…) и мусор (.git/venv/node_modules/__pycache__/…) НЕ читаем — иначе ключи
# утекли бы в промпт LLM. Только текст (код+доки), крупные файлы обрезаны по размеру.
_FILES_TEXT_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php",
    ".c", ".h", ".hpp", ".cpp", ".cc", ".cs", ".swift", ".m", ".mm", ".scala", ".dart",
    ".lua", ".r", ".jl", ".sh", ".sql", ".vue", ".svelte", ".html",
    ".md", ".txt", ".rst", ".markdown", ".adoc",
}
_FILES_SKIP_DIRS = {
    ".git", ".hg", ".svn", "venv", ".venv", "env", "node_modules", "__pycache__",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next",
    ".cache", "dist", "build", "target", "vendor", "coverage", "htmlcov",
}
_FILES_SECRET_EXT = {".env", ".session", ".key", ".pem", ".pfx", ".p12", ".crt",
                     ".cer", ".keystore", ".jks", ".ppk"}
_FILES_SECRET_HINTS = ("secret", "password", "credential", "token", "apikey",
                       "api_key", "id_rsa", ".htpasswd")
_FILES_MAX_BYTES = 256 * 1024   # крупные файлы не тянем (заголовок всё равно берём из «шапки»)
_FILES_HEAD_BYTES = 4096        # хватает на первую содержательную строку
_FILES_MAX_SCAN = 20000         # предохранитель: осматриваем не больше стольких файлов за прогон —
                                # ошибочно заданный диск-корень («M:/») не заставит обойти весь диск
                                # и подвесить тик автосбора (реальному проекту 20k файлов с запасом)

# Строка-СЕКРЕТ — НЕ берём её в заголовок. Заголовок уходит в промпт LLM (ideate) ДО
# scrub_secrets, поэтому имя-фильтра (_files_is_secret) мало: секрет бывает в СОДЕРЖИМОМ файла
# с обычным именем (config.py: API_KEY="…", bot.py: TOKEN="…"). Ловим формы значений (ключи/
# токены/JWT/telegram-token/creds-в-URL) И присваивания с секрет-ключевым словом. stdlib-only:
# collect_source не тянет cyborg/organs_vendored/scrub_secrets (свой компактный набор здесь).
_FILES_SECRET_LINE = re.compile(
    r"sk-[A-Za-z0-9_-]{12,}"
    r"|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r"|AKIA[A-Z0-9]{12,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"                 # JWT
    r"|\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"                          # telegram bot token
    r"|[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/@]+:[^\s/@]+@"           # scheme://user:pass@host
    r"|(?i:\w*(?:api[_-]?key|secret|token|passw(?:or)?d|credential|access[_-]?key)\w*)\s*[:=]\s*\S"
)


def _files_is_secret(name):
    """Имя похоже на секрет (по расширению или подстроке) -> не читаем вовсе."""
    low = name.lower()
    if os.path.splitext(low)[1] in _FILES_SECRET_EXT:
        return True
    return any(h in low for h in _FILES_SECRET_HINTS)


def _files_headline(path):
    """Первая СОДЕРЖАТЕЛЬНАЯ строка файла как заголовок: снимаем обёртки (кавычки докстринга,
    маркеры комментов, markdown-#), пропускаем техническое (shebang, coding, import) И строки-
    СЕКРЕТЫ (значение ключа/токена/пароля не должно утечь в промпт LLM). Пусто — нет пригодной
    строки (тогда заголовком остаётся просто имя файла)."""
    try:
        with open(path, "rb") as f:
            head = f.read(_FILES_HEAD_BYTES)
    except OSError:
        return ""
    # utf-8-sig снимает BOM (иначе '﻿' в начале ломает и заголовок, и проверку shebang)
    for raw in head.decode("utf-8-sig", errors="replace").splitlines():
        s = raw.strip().lstrip("﻿").strip()
        if not s:
            continue
        low_raw = s.lower()
        # технические первые строки — не заголовок. coding — ТОЛЬКО PEP-263 форма («coding:»/
        # «coding=»/«-*-»), а не любое слово «coding» (иначе срезали бы «# Coding standards»).
        if (low_raw.startswith(("#!", "<!doctype", "<?xml"))
                or (low_raw.startswith("#")
                    and ("-*-" in low_raw or "coding:" in low_raw or "coding=" in low_raw))):
            continue
        line = s.lstrip("#/*-;%=<>! \t").strip().strip('"').strip("'").strip("`").strip()
        low = line.lower()
        if not line or low.startswith(("import ", "from ", "package ", "use ",
                                        "#include", "using ")):
            continue
        if _FILES_SECRET_LINE.search(line):
            continue                      # строка-секрет (ключ/токен/пароль/creds-URL) — не в заголовок
        return line[:180]
    return ""


def _files_is_candidate(path):
    """Файл годится как текстовое сырьё: имя не секрет, текстовое расширение, в пределах размера.
    ЕДИНЫЙ фильтр для _files (реальный сбор) и probe_paths (счётчик пульта) — одна правда, без
    дубля: правишь фильтр здесь → и сбор, и проба меняются согласованно."""
    name = os.path.basename(path)
    if _files_is_secret(name):            # секреты не читаем вообще (не утекут в промпт LLM)
        return False
    if os.path.splitext(name)[1].lower() not in _FILES_TEXT_EXT:   # только текст (код+доки)
        return False
    try:
        return os.path.getsize(path) <= _FILES_MAX_BYTES           # крупные — мимо
    except OSError:
        return False


def _files_walk(root):
    """Лениво обходит папку, обрубая мусорные/скрытые подпапки. Генератор (не список): при
    упоре в потолок _FILES_MAX_SCAN вызыватель просто перестаёт тянуть — обход не идёт дальше."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _FILES_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            yield (os.path.join(dirpath, fn), root)


def _files(n, timeout, env):
    roots = list(env.get("files_paths") or [])
    if not roots:
        raise ValueError("files: no folders configured (env['files_paths'])")
    found = []                            # [(полный_путь, база_для_относительного)]
    scanned = 0                           # предохранитель: не осматриваем больше _FILES_MAX_SCAN файлов
    for root in roots:
        if scanned >= _FILES_MAX_SCAN:
            break
        if os.path.isfile(root):
            cand = [(root, os.path.dirname(root))]
        elif os.path.isdir(root):
            cand = _files_walk(root)      # ленивый обход (обрывается при упоре в потолок)
        else:
            continue                      # путь не существует — молча пропускаем (не крашим)
        for p, base in cand:
            scanned += 1
            if scanned > _FILES_MAX_SCAN:  # потолок файлов — дальше не идём (тик не виснет на диске)
                break
            if _files_is_candidate(p):     # секрет/не-текст/крупный — мимо (общий фильтр с probe_paths)
                found.append((p, base))
    if not found:
        raise ValueError("files: no readable text files in configured folders")
    # папка шире бюджета n -> случайная выборка (ротация, как у telegram): за разные прогоны
    # смотрим разные файлы, а не всегда первые n
    if len(found) > n:
        found = random.sample(found, n)
    items = []
    for p, base in found:
        rel = os.path.relpath(p, base) if base else os.path.basename(p)
        headline = _files_headline(p)
        title = (f"{rel} — {headline}" if headline else rel)[:200]
        items.append({"title": title, "url": "", "id": os.path.abspath(p)})  # abspath — стабильный id
    return items


def probe_paths(paths):
    """Дёшево (без чтения СОДЕРЖИМОГО файлов) оценить папки-источник для пульта: по каждому пути —
    существует ли он и сколько в нём ПРИГОДНЫХ текстовых файлов (тем же фильтром _files_is_candidate,
    что и реальный сбор). Юзер сразу видит, что путь верный, ДО прогона: опечатка в пути → «не
    найдено» или 0 файлов на виду, а не молчаливый ноль в автосборе. Обход капается _FILES_MAX_SCAN
    СУММАРНО по всем путям (как реальный прогон) — ошибочный диск-корень не подвесит запрос пульта.
    -> {путь: {"exists": bool, "files": int, "capped": bool}} (capped=обход обрезан потолком)."""
    result = {}
    scanned = 0
    for root in (paths or []):
        if not isinstance(root, str) or not root.strip():
            continue
        if os.path.isfile(root):
            entries = [(root, None)]
        elif os.path.isdir(root):
            entries = _files_walk(root)
        else:
            result[root] = {"exists": False, "files": 0, "capped": False}   # путь не существует
            continue
        cnt, capped = 0, False
        for p, _base in entries:
            if scanned >= _FILES_MAX_SCAN:
                capped = True                 # обход обрезан — счётчик неполон, честно помечаем
                break
            scanned += 1
            if _files_is_candidate(p):
                cnt += 1
        result[root] = {"exists": True, "files": cnt, "capped": capped}
    return result


_SOURCES = {
    "hn": _hn,
    "reddit": _reddit,
    "lobsters": _lobsters,
    "gh_trending": _gh_trending,
    "telegram": _telegram,
    "files": _files,
}


def run(inputs, env):
    env = env or {}
    n = int(env.get("n", 8))
    timeout = float(env.get("timeout", 8))
    sources = env.get("sources")
    if sources is not None and not sources:
        # ЯВНО пустой список (все ленты выключены в пульте И папок нет) — НЕ дефолтим на hn и НЕ
        # выдаём _FALLBACK. Контракт harvest._active_sources: пусто → не собираем, пульт предупреждает.
        # Иначе выключение всех тумблеров молча тащило бы HN, вопреки им (аудит 2026-07-17, D7).
        return {"items": [], "source": "", "degraded": True,
                "degraded_reason": "нет источников: включи ленту в пульте или добавь папку"}
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
