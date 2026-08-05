# fmt: off
# Замороженный орган (гейт человека, см. README): collect_source — ядро сбора/маршрутизации.
# Black/ruff НЕ форматируют этот файл — стабильность важнее единообразия стиля.
# Маркер # fmt: off — документированная гарантия black.
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
дедуп в harvest). Неизвестный источник или сетевой сбой -> честный пустой список,
degraded=True. Один источник упал, но другие дали сырьё -> НЕ degraded (сырьё есть),
но ошибка видна в partial_errors — для диагностики, без блокировки органа.
"""
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cyborg"))
import config  # noqa: E402

HN_TOP = config.HN_TOP_URL
HN_SHOW = config.HN_SHOW_URL
HN_ITEM = config.HN_ITEM_URL
REDDIT_TOP = config.REDDIT_TOP_URL
LOBSTERS_HOT = config.LOBSTERS_HOT_URL
GH_TRENDING = config.GH_TRENDING_URL
_UA = config.HTTP_USER_AGENT
_GH_ENRICH_DEFAULT_LIMIT = config.GH_TRENDING_ENRICH_LIMIT  # GitHub без токена: максимум 60 API-запросов/час

def _get(url_or_req, timeout):
    with urllib.request.urlopen(url_or_req, timeout=timeout) as r:
        return json.loads(r.read().decode(config.HTTP_CHARSET_UTF8))


def _hn_fetch_ids(list_url, n, timeout):
    """Тянуть n item-id из HN-листа (topstories/showstories) и собрать {title,url,id}. Посты без
    title отбрасываются. Пусто/сбой -> ValueError (вызывающий решает, как реагировать)."""
    ids = _get(list_url, timeout)[:n]
    items = []
    for i in ids:
        it = _get(HN_ITEM.format(i), timeout)
        if it and it.get("title"):
            items.append({"title": it["title"], "url": it.get("url", ""), "id": it.get("id")})
    if not items:
        raise ValueError("hn returned empty")
    return items


def _hn(n, timeout, env):
    # hn_show_mix=True: половина бюджета из topstories (тренды/обсуждения), половина из showstories
    # (Show HN — реальные проекты). Топ HN засорён новостями/некрологами; Show HN — чистое
    # проектное топливо. Смешивание даёт оба жанра, не теряя тренды. Без флага — только топ (как
    # раньше). Один источник упал -> берём сколько есть из другого (degrade, не краш).
    if not env.get("hn_show_mix"):
        return _hn_fetch_ids(HN_TOP, n, timeout)
    half = max(1, n // 2)
    out = []
    try:
        out.extend(_hn_fetch_ids(HN_TOP, half, timeout))
    except Exception:
        pass
    try:
        out.extend(_hn_fetch_ids(HN_SHOW, n - len(out), timeout))
    except Exception:
        pass
    if not out:
        raise ValueError("hn returned empty")
    return out[:n]


def _reddit(n, timeout, env):
    # без User-Agent reddit отвечает 429 — ставим свой (публичный .json-эндпоинт, без ключа)
    req = urllib.request.Request(REDDIT_TOP.format(n), headers={config.HTTP_HEADER_USER_AGENT: _UA})
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


def _gh_repo_description(owner, repo, timeout):
    # GitHub публичный API без токена: 60 запросов/час с IP. /repos/{o}/{r} даёт description,
    # превращая слепой «owner/repo» (из trending-скрейпа) в осмысленную карточку. Необязательный
    # enrich — при лимите/сбое вызывающий падает на голый owner/repo. Только stdlib (_get).
    data = _get(config.GH_REPO_API_URL.format(owner=owner, repo=repo), timeout)
    desc = (data.get("description") or "").strip() if isinstance(data, dict) else ""
    return desc[: config.GH_REPO_DESCRIPTION_MAX_CHARS]


def _gh_trending(n, timeout, env):
    # официального API нет -> HTML-скрейп; парсим ТЕРПИМО (только class~lh-condensed + href
    # owner/repo), любая непонятная разметка -> ValueError -> честный degrade, не краш.
    req = urllib.request.Request(
        GH_TRENDING,
        headers={
            config.HTTP_HEADER_USER_AGENT: (
                config.HTTP_USER_AGENT_MOZILLA_PREFIX + _UA + config.HTTP_USER_AGENT_MOZILLA_SUFFIX
            )
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode(config.HTTP_CHARSET_UTF8, errors=config.HTTP_DECODE_ERRORS_REPLACE)
    blocks = re.findall(r'<h2[^>]*class="[^"]*lh-condensed[^"]*"[^>]*>(.*?)</h2>', html, re.DOTALL)
    items = []
    try:
        enrich_left = max(0, int(env.get("gh_enrich_limit", _GH_ENRICH_DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        enrich_left = _GH_ENRICH_DEFAULT_LIMIT
    for b in blocks[:n]:
        m = re.search(r'href="/([^"/?]+)/([^"/?]+)"', b)
        if m:
            owner, repo = m.group(1), m.group(2)
            title = f"{owner}/{repo}"
            # enrich: gh_enrich=True (прод через harvest_env) — тянем description из API. Слепой
            # «owner/repo» как топливо идей слаб: совет не знает что это за репо. Description даёт
            # осмысленный заголовок. Неудача (лимит/сеть) — тихо, fallback на голый owner/repo.
            if env.get("gh_enrich") and enrich_left:
                enrich_left -= 1
                try:
                    desc = _gh_repo_description(owner, repo, timeout)
                    if desc:
                        title = f"{owner}/{repo} — {desc}"
                except Exception:
                    pass
            items.append({"title": title, "url": f"https://github.com/{owner}/{repo}",
                          "id": f"{owner}/{repo}"})  # repo сам по себе стабильный id (для дедупа items)
    if not items:
        raise ValueError("gh_trending: no repos parsed")
    return items


# Telegram — единственный КЛЮЧЕВОЙ источник: читает через pyrogram (личный ТГ-аккаунт), а не
# публичный API. pyrogram — НЕ stdlib, поэтому вызываем его отдельным процессом на venv darbot
# (там уже стоит) в режиме --rpc; сама collect_source.py stdlib-only остаётся (только subprocess).
# Вендоренный орган: cyborg/organs_vendored/collect_tg_news.py (копия darbot/organ.py, EXTRACT_ORGAN).
# Дефолтный путь к Python darbot-venv централизован в cyborg/config.py (единый источник истины).
_TG_PYTHON_DEFAULT = config.DARBOT_PYTHON
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
    }).encode(config.HTTP_CHARSET_UTF8)
    # timeout шире, чем у HTTP-источников: логин pyrogram-клиента + запуск отдельного питона —
    # дороже одного GET; env["telegram_timeout"] можно поднять отдельно, не трогая общий timeout.
    tg_timeout = float(env.get("telegram_timeout", max(timeout, 25)))
    proc = subprocess.run([python_exe, _TG_RUNNER, "--rpc"], input=payload,
                           capture_output=True, timeout=tg_timeout)
    if proc.returncode != 0:
        raise ValueError(f"telegram: rpc exit {proc.returncode}: {proc.stderr.decode(config.HTTP_CHARSET_UTF8, config.HTTP_DECODE_ERRORS_REPLACE)[: config.TELEGRAM_RPC_ERROR_MAX_CHARS]}")
    result = json.loads(proc.stdout.decode(config.HTTP_CHARSET_UTF8))

    items = []
    for it in result.get("items", []):
        title = (it.get("text") or "").strip().splitlines()[0][: config.TELEGRAM_POST_TITLE_MAX_CHARS] if it.get("text") else ""
        if title:
            items.append({"title": title, "url": it.get("url") or "",
                          "id": f"{it.get('channel')}:{it.get('id')}"})
    if not items:
        reason = "; ".join(result.get("warnings", [])) or "no posts"
        raise ValueError(f"telegram returned empty: {reason}")
    return items[:n]


# ── Источник «files»: читает ТЕКСТОВЫЕ файлы из заданных папок как ещё одно сырьё для идей ──
# Смотрит на папку НЕЙТРАЛЬНО — как на чужой проект со стороны (не «свой код», без «чини себя»).
# Один файл = один item: {относительный_путь — первая содержательная строка} + короткий
# содержательный фрагмент. Фрагмент нужен для саморефлексии: по одному имени файла и докстрингу
# модель видела лишь «обложку» проекта и выдумывала проблемы. Контекст жёстко ограничен по числу
# файлов/строк/символов, иначе широкий source_n раздует промпт. Настройки — env["files_paths"]
# (список папок и/или отдельных файлов); без них честный ValueError -> degrade.
# БЕЗОПАСНОСТЬ: секреты (*.env/*.session/ключи и имена с secret/token/…) и мусор
# (.git/venv/node_modules/__pycache__/…) НЕ читаем. Строки, похожие на креды, выкидываются здесь,
# а wiring_collect повторно скрабит title+context перед LLM. Только текст (код+доки).
_FILES_TEXT_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php",
    ".c", ".h", ".hpp", ".cpp", ".cc", ".cs", ".swift", ".m", ".mm", ".scala", ".dart",
    ".lua", ".r", ".jl", ".sh", ".sql", ".vue", ".svelte", ".html",
    ".md", ".txt", ".rst", ".markdown", ".adoc",
    # Конфигурация и инфраструктура — без них «анализ проекта» слеп к реальной сборке.
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".xml", ".css", ".scss", ".sass", ".less", ".ps1", ".psm1", ".bat", ".cmd",
    ".gradle", ".proto", ".graphql", ".gql", ".tf", ".hcl",
}
_FILES_TEXT_NAMES = {
    "dockerfile", "makefile", "rakefile", "gemfile", "procfile", "justfile",
    "go.mod", "go.work", "cargo.lock",
}
_FILES_LOW_SIGNAL_NAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "go.sum", "cargo.lock",
}
_FILES_SKIP_DIRS = {
    ".git", ".hg", ".svn", "venv", ".venv", "env", "node_modules", "__pycache__",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next",
    ".cache", "dist", "build", "target", "vendor", "coverage", "htmlcov",
    # История чатов и бэкапы забивают случайную выборку устаревшими снимками. Если они нужны
    # как сырьё, их можно указать отдельным корнем: _files_walk не отбрасывает сам root.
    "handoffs", "backups",
}
# Эти скрытые папки — не личный мусор, а часть архитектуры/деплоя проекта.
_FILES_KEEP_HIDDEN_DIRS = {".github", ".openai"}
_FILES_SECRET_EXT = {".env", ".session", ".key", ".pem", ".pfx", ".p12", ".crt",
                     ".cer", ".keystore", ".jks", ".ppk"}
_FILES_SECRET_HINTS = ("secret", "password", "credential", "token", "apikey",
                       "api_key", "id_rsa", ".htpasswd")
_FILES_MAX_BYTES = config.FILES_MAX_BYTES  # до 1 МиБ: большой исходник читаем, но выдаём только умную выжимку
_FILES_HEAD_BYTES = config.FILES_HEAD_BYTES  # headline переживает длинную лицензионную/генерированную шапку
_FILES_CONTEXT_BYTES = config.FILES_MAX_BYTES  # ищем TODO/символы/ошибки по всему допустимому файлу
_FILES_CONTEXT_CHARS = config.FILES_CONTEXT_CHARS  # глубже прежних 700, но один файл не захватывает весь промпт
_FILES_CONTEXT_LINES = config.FILES_CONTEXT_LINES  # линии выбираются по смыслу и по разным участкам файла
_FILES_MAX_ITEMS = config.FILES_MAX_ITEMS  # 48×700 ≈ 34k символов контекста — безопасный потолок прогона
_FILES_MAX_SCAN = config.FILES_MAX_SCAN  # предохранитель: осматриваем не больше стольких файлов за прогон —
                                # ошибочно заданный диск-корень («M:/») не заставит обойти весь диск
                                # и подвесить тик автосбора (реальному проекту 20k файлов с запасом)
_FILES_MAX_PROJECT_MAPS = config.FILES_MAX_PROJECT_MAPS  # карта не должна вытеснить файлы при десятках отдельных корней

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
    r"|(?i:[A-Za-z0-9_]{0,40}(?:api[_-]?key|secret|token|passw(?:or)?d|credential|access[_-]?key)"
    r"[A-Za-z0-9_]{0,40})\s*[:=]\s*\S"
)


def _files_is_secret(name):
    """Имя похоже на секрет (по расширению или подстроке) -> не читаем вовсе."""
    low = name.lower()
    if os.path.splitext(low)[1] in _FILES_SECRET_EXT:
        return True
    # Не режем полезные tokenizer.py / secret_scanner.py только из-за куска слова.
    # Секретный намёк должен быть отдельной частью имени: my_token.txt, api_key-prod.json.
    parts = set(p for p in re.split(r"[^a-z0-9]+", low) if p)
    compact = low.replace("-", "_")
    return (
        bool(parts & {"secret", "secrets", "password", "passwords", "credential", "credentials",
                      "token", "tokens", "apikey", "htpasswd"})
        or "api_key" in compact
        or low.startswith("id_rsa")
    )


def _files_decode(raw):
    """Декодировать текст без кракозябр.

    Большинство проектов UTF-8, но старые Windows-файлы бывают CP1251, а один файл может
    содержать строки разных эпох. Поэтому UTF-16 распознаём целиком, остальные кодируем
    построчно: UTF-8 -> CP1251 -> latin-1. Ошибочные байты не превращают весь русский файл
    в mojibake.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    # UTF-16 без BOM: много нулей на чётных/нечётных позициях.
    if raw and raw.count(b"\x00") > len(raw) // 5:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    decoded = []
    for idx, line in enumerate(raw.splitlines()):
        if idx == 0 and line.startswith(b"\xef\xbb\xbf"):
            line = line[3:]
        try:
            decoded.append(line.decode(config.HTTP_CHARSET_UTF8))
        except UnicodeDecodeError:
            try:
                decoded.append(line.decode(config.FILES_DECODE_ENCODING_CP1251))
            except UnicodeDecodeError:
                decoded.append(line.decode(config.FILES_DECODE_ENCODING_LATIN1, errors=config.HTTP_DECODE_ERRORS_REPLACE))
    return "\n".join(decoded)


def _files_read_lines(path, limit):
    try:
        with open(path, "rb") as f:
            raw = f.read(limit)
    except OSError:
        return []
    return list(enumerate(_files_decode(raw).splitlines(), 1))


def _files_safe_line(raw):
    """Одна строка, которую разрешено отдать модели; отступ сохраняем."""
    expanded = str(raw or "").expandtabs(4).rstrip()
    stripped = expanded.strip().lstrip("\ufeff").strip()
    if not stripped:
        return ""
    # Минифицированная/сгенерированная строка может занимать сотни КБ. Полный regex с
    # `\w*` на ней дорог и сама строка бесполезна; проверяем края и берём только начало.
    secret_probe = (
        stripped
        if len(stripped) <= config.FILES_SECRET_PROBE_FULL_WINDOW
        else stripped[: config.FILES_SECRET_PROBE_HALF_WINDOW] + stripped[-config.FILES_SECRET_PROBE_HALF_WINDOW :]
    )
    if _FILES_SECRET_LINE.search(secret_probe):
        return ""
    # Бинарь под текстовым расширением: управляющие символы — сильный сигнал.
    if sum(1 for ch in stripped if ord(ch) < 32 and ch not in "\t") > 1:
        return ""
    indent = min(len(expanded) - len(expanded.lstrip()), 24)
    body = expanded.lstrip()
    return (" " * indent + body)[: config.FILES_SAFE_LINE_MAX_CHARS]


def _files_meaningful_lines(path, limit):
    out = []
    for lineno, raw in _files_read_lines(path, limit):
        line = _files_safe_line(raw)
        if not line:
            continue
        stripped = line.strip()
        if stripped in {"{", "}", "[", "]", "(", ")", ");", "};", "---", "***", '"""', "'''"}:
            continue
        out.append((lineno, line))
    return out


def _files_headline(path):
    """Первая СОДЕРЖАТЕЛЬНАЯ строка файла как заголовок: снимаем обёртки (кавычки докстринга,
    маркеры комментов, markdown-#), пропускаем техническое (shebang, coding, import) И строки-
    СЕКРЕТЫ (значение ключа/токена/пароля не должно утечь в промпт LLM). Пусто — нет пригодной
    строки (тогда заголовком остаётся просто имя файла)."""
    for _lineno, raw in _files_read_lines(path, config.FILES_HEAD_BYTES):
        s = _files_safe_line(raw).strip()
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
        return line[: config.FILES_HEADLINE_MAX_CHARS]
    return ""


_FILES_SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|func|interface|type|struct|enum|trait|impl|record)\s+"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*="
    r"|^\s*(?:public|private|protected|internal|static|final|abstract|sealed)\s+.*(?:class|interface)\s+"
    r"|^\s*(?:CREATE\s+(?:TABLE|VIEW|FUNCTION|PROCEDURE)|ALTER\s+TABLE)\b",
    re.IGNORECASE,
)
_FILES_RISK_RE = re.compile(
    r"\b(?:TODO|FIXME|BUG|HACK|XXX|deprecated|raise|except|catch|panic|fatal|rollback|retry|timeout)\b",
    re.IGNORECASE,
)
_FILES_CONFIG_RE = re.compile(
    r"^\s*[\"']?[A-Za-z_][\w.\-]*[\"']?\s*[:=]"
    r"|^\s*<(?:service|component|dependency|route|target|task|property)\b",
    re.IGNORECASE,
)


def _spread_pick(rows, limit):
    """Взять строки со всего файла, а не только первые совпадения."""
    if limit <= 0 or not rows:
        return []
    if len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[len(rows) // 2]]
    idxs = {round(i * (len(rows) - 1) / (limit - 1)) for i in range(limit)}
    return [rows[i] for i in sorted(idxs)]


def _files_dependencies(lines):
    deps = []
    patterns = (
        re.compile(r"^\s*from\s+([\w.]+)\s+import\b"),
        re.compile(r"^\s*import\s+([\w.@/\-]+)"),
        re.compile(r"^\s*use\s+([\w:]+)"),
        re.compile(r"^\s*#include\s*[<\"]([^>\"]+)"),
        re.compile(r"^\s*.*\bfrom\s+[\"']([^\"']+)[\"']"),
        re.compile(r"^\s*.*\brequire\([\"']([^\"']+)[\"']\)"),
    )
    seen = set()
    for _lineno, line in lines:
        for pat in patterns:
            match = pat.match(line)
            if not match:
                continue
            dep = match.group(1).strip()
            if dep and dep not in seen:
                seen.add(dep)
                deps.append(dep)
            break
        if len(deps) >= config.FILES_DEPS_MAX_ITEMS:
            break
    return deps


def _files_context(path, headline=""):
    """Безопасная выжимка ПО ВСЕМУ файлу: шапка, символы, риски, конфиг и хвост.

    Это всё ещё не полный файл в prompt, но модель видит функции/TODO далеко ниже шапки,
    номера строк, зависимости и исходные отступы. Выбор строк распределён по файлу.
    """
    rows = _files_meaningful_lines(path, config.FILES_MAX_BYTES)
    if not rows:
        return ""
    headline_norm = re.sub(r"\s+", " ", str(headline or "")).strip().casefold()

    def is_headline(row):
        comparable = row[1].strip().lstrip("#/*-;%=<>! \t").strip().strip("\"'`").casefold()
        return bool(headline_norm and comparable == headline_norm)

    usable = [row for row in rows if not is_headline(row)]
    technical = ("import ", "from ", "package ", "use ", "#include", "using ")
    content_rows = [row for row in usable if not row[1].strip().lower().startswith(technical)]
    head = content_rows[:3]
    risks = _spread_pick([row for row in content_rows if _FILES_RISK_RE.search(row[1])], 4)
    symbols = _spread_pick([row for row in content_rows if _FILES_SYMBOL_RE.search(row[1])], 7)
    configs = _spread_pick([row for row in content_rows if _FILES_CONFIG_RE.search(row[1])], 4)
    distributed = _spread_pick(content_rows, 4)
    tail = content_rows[-2:]

    selected, seen = [], set()
    # Сначала самые доказательные сигналы, чтобы они не отпали по символьному потолку.
    for group in (head, risks, symbols, configs, distributed, tail):
        for row in group:
            key = (row[0], row[1].strip().casefold())
            if key in seen:
                continue
            seen.add(key)
            selected.append(row)
            if len(selected) >= config.FILES_CONTEXT_LINES:
                break
        if len(selected) >= config.FILES_CONTEXT_LINES:
            break

    parts = []
    deps = _files_dependencies(rows)
    if deps:
        parts.append("Связи: " + ", ".join(deps))
    for lineno, line in selected:
        parts.append(f"L{lineno}: {line[: config.FILES_CONTEXT_LINE_MAX_CHARS]}")
    return "\n".join(parts)[: config.FILES_CONTEXT_CHARS]


def _files_is_candidate(path):
    """Файл годится как текстовое сырьё: имя не секрет, текстовое расширение, в пределах размера.
    ЕДИНЫЙ фильтр для _files (реальный сбор) и probe_paths (счётчик пульта) — одна правда, без
    дубля: правишь фильтр здесь → и сбор, и проба меняются согласованно."""
    name = os.path.basename(path)
    if _files_is_secret(name):            # секреты не читаем вообще (не утекут в промпт LLM)
        return False
    low_name = name.lower()
    if (
        os.path.splitext(low_name)[1] not in _FILES_TEXT_EXT
        and low_name not in _FILES_TEXT_NAMES
    ):   # только известный текст (код+доки+конфиг)
        return False
    try:
        return os.path.getsize(path) <= config.FILES_MAX_BYTES
    except OSError:
        return False


def _files_path_allowed(path, root):
    rel = os.path.relpath(path, root).replace("\\", "/")
    dirs = rel.split("/")[:-1]
    skip = {d.casefold() for d in _FILES_SKIP_DIRS}
    for directory in dirs:
        low = directory.casefold()
        if low in skip:
            return False
        if directory.startswith(".") and low not in _FILES_KEEP_HIDDEN_DIRS:
            return False
    return True


def _files_git_walk(root):
    """Файлы Git-репозитория с учётом .gitignore; нет Git/репо -> None."""
    if not os.path.exists(os.path.join(root, ".git")):
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=config.FILES_GIT_LSFILES_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    paths = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode(config.HTTP_CHARSET_UTF8, errors=config.HTTP_DECODE_ERRORS_SURROGATEESCAPE)
        path = os.path.join(root, *rel.split("/"))
        if os.path.isfile(path) and _files_path_allowed(path, root):
            paths.append(path)
    return sorted(set(paths), key=lambda p: p.casefold())


def _files_walk(root):
    """Обходит проект детерминированно и уважает .gitignore, если это Git-репозиторий."""
    git_paths = _files_git_walk(root)
    if git_paths is not None:
        for path in git_paths:
            yield (path, root)
        return
    skip = {d.casefold() for d in _FILES_SKIP_DIRS}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            (
                d for d in dirnames
                if d.casefold() not in skip
                and (not d.startswith(".") or d.casefold() in _FILES_KEEP_HIDDEN_DIRS)
            ),
            key=str.casefold,
        )
        for fn in sorted(filenames, key=str.casefold):
            path = os.path.join(dirpath, fn)
            if _files_path_allowed(path, root):
                yield (path, root)


def _files_item_id(path, base, headline, context=""):
    """Непрозрачный v2-id файла для seen_items.

    Внутри одного корня различаем ОТНОСИТЕЛЬНЫЙ путь, поэтому main.py из двух
    папок не сливаются. Имя корня + относительный путь переживают перенос самого
    проекта на другой диск. Видимый headline входит в отпечаток: если именно то
    сырьё, которое увидит LLM, изменилось, файл честно становится новым.
    """
    norm_path = str(path).replace("\\", "/")
    norm_base = str(base or "").replace("\\", "/").rstrip("/")
    low_path, low_base = norm_path.casefold(), norm_base.casefold()
    if low_base and low_path.startswith(low_base + "/"):
        rel = norm_path[len(norm_base) + 1 :]
    else:
        rel = norm_path.rsplit("/", 1)[-1]
    root_label = norm_base.rsplit("/", 1)[-1] if norm_base else ""
    # Контекст входит в ID: изменение кода ниже неизменной первой строки должно снова стать
    # «свежим» сырьём. Иначе seen_items навсегда скрывал бы доработанный файл от саморефлексии.
    identity = f"{root_label}/{rel}\n{headline or ''}\n{context or ''}".casefold()
    return "f2:" + hashlib.sha1(identity.encode(config.HTTP_CHARSET_UTF8)).hexdigest()[:16]


_FILES_OVERVIEW_NAMES = {
    "readme.md", "readme.txt", "agents.md", "contributing.md", "architecture.md",
    "design.md", "decisions.md", "roadmap.md", "runbook.md",
}
_FILES_MANIFEST_NAMES = {
    "pyproject.toml", "package.json", "cargo.toml", "go.mod", "go.work",
    "requirements.txt", "dockerfile", "compose.yaml", "compose.yml",
    "docker-compose.yaml", "docker-compose.yml", "makefile",
}
_FILES_ENTRY_NAMES = {
    "main.py", "app.py", "server.py", "cli.py", "index.js", "index.ts",
    "main.go", "main.rs", "program.cs",
}


def _files_root_label(base):
    norm = os.path.normpath(os.fspath(base or ""))
    label = os.path.basename(norm)
    return label or norm.replace("\\", "/") or "project"


def _files_role(path, base):
    rel = os.path.relpath(path, base).replace("\\", "/")
    low = rel.casefold()
    name = os.path.basename(low)
    parts = low.split("/")
    ext = os.path.splitext(name)[1]
    if any(p in {"archive", "archives", "legacy", "deprecated"} for p in parts[:-1]):
        return "archive", 12
    if any(p in {"audit", "audits", "codex_update"} for p in parts[:-1]):
        return "audit", 30
    if name in _FILES_OVERVIEW_NAMES:
        return "overview", 120
    if name in _FILES_MANIFEST_NAMES or name in _FILES_TEXT_NAMES:
        return "manifest", 112
    if name in _FILES_ENTRY_NAMES:
        return "entrypoint", 104
    if name in _FILES_LOW_SIGNAL_NAMES or ".min." in name or name.endswith(".map"):
        return "generated", 5
    if any(p in {"tests", "test", "spec", "specs"} for p in parts[:-1]) or name.startswith("test_"):
        return "test", 36
    if any(p in {"docs", "doc", "knowledge"} for p in parts[:-1]) or ext in {".md", ".rst", ".adoc"}:
        return "docs", 62
    if ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".tf", ".hcl"}:
        return "config", 78
    if any(word in name for word in ("core", "config", "router", "service", "controller", "model", "store", "api")):
        return "core", 82
    return "source", 58


def _files_area(rel):
    parts = rel.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return "(root)"
    if len(parts) > 2 and parts[1].casefold() in {
        "tests", "test", "docs", "doc", "knowledge", "archive", "legacy", "audit", "audits", "codex_update",
    }:
        return "/".join(parts[:2])
    return parts[0]


def _files_record(path, base):
    rel = os.path.relpath(path, base).replace("\\", "/")
    role, score = _files_role(path, base)
    return {
        "path": path,
        "base": base,
        "project": _files_root_label(base),
        "rel": rel,
        "area": _files_area(rel),
        "role": role,
        "score": score,
    }


def _files_select(records, budget):
    """Сбалансированный отбор: каждый корень и каждая зона получают голос.

    Ключевой обзор/manifest/entrypoint идут первыми; остальное ротируется случайным
    tie-breaker. Тесты и архив не могут захватить больше разумной доли prompt.
    """
    if len(records) <= budget:
        return sorted(records, key=lambda r: (-r["score"], r["rel"].casefold()))

    work = [dict(rec, _order=rec["score"] + random.random() * config.FILES_SELECT_JITTER) for rec in records]
    by_root = {}
    for rec in work:
        by_root.setdefault(os.path.normcase(os.path.abspath(rec["base"])), []).append(rec)
    roots = sorted(by_root, key=lambda key: _files_root_label(by_root[key][0]["base"]).casefold())

    selected, chosen = [], set()
    role_counts = {"test": 0, "archive": 0, "audit": 0, "generated": 0}
    role_caps = {
        "test": max(2, budget // 6),
        "archive": max(1, budget // 12),
        "audit": max(2, budget // 8),
        "generated": 1,
    }

    def allowed(rec):
        cap = role_caps.get(rec["role"])
        return cap is None or role_counts.get(rec["role"], 0) < cap

    def take(rec):
        key = os.path.normcase(os.path.abspath(rec["path"]))
        if key in chosen or not allowed(rec):
            return False
        chosen.add(key)
        selected.append(rec)
        if rec["role"] in role_counts:
            role_counts[rec["role"]] += 1
        return True

    # По одной архитектурной опоре на каждый отдельный корень.
    for root in roots:
        best = max(by_root[root], key=lambda r: (r["_order"], -len(r["rel"])))
        take(best)
        if len(selected) >= budget:
            return selected[:budget]

    # Внутри корня ходим по зонам round-robin. Поэтому M:/projects не отдаёт весь
    # бюджет двум самым большим репозиториям, а отдельные roots делят его поровну.
    root_areas = {}
    root_pos = {}
    for root in roots:
        areas = {}
        for rec in by_root[root]:
            areas.setdefault(rec["area"], []).append(rec)
        for rows in areas.values():
            rows.sort(key=lambda r: (-r["_order"], r["rel"].casefold()))
        order = sorted(areas, key=lambda a: (-max(r["_order"] for r in areas[a]), a.casefold()))
        root_areas[root] = (areas, order)
        root_pos[root] = 0

    stalled_rounds = 0
    while len(selected) < budget and stalled_rounds < 2:
        progress = False
        for root in roots:
            areas, order = root_areas[root]
            if not order:
                continue
            for _ in range(len(order)):
                idx = root_pos[root] % len(order)
                root_pos[root] += 1
                area = order[idx]
                rows = areas[area]
                while rows and os.path.normcase(os.path.abspath(rows[0]["path"])) in chosen:
                    rows.pop(0)
                if not rows:
                    continue
                # Если верхний test/archive упёрся в квоту, ищем следующий полезный в зоне.
                pick_idx = next((i for i, rec in enumerate(rows) if allowed(rec)), None)
                if pick_idx is None:
                    continue
                rec = rows.pop(pick_idx)
                if take(rec):
                    progress = True
                break
            if len(selected) >= budget:
                break
        stalled_rounds = 0 if progress else stalled_rounds + 1

    # Если строгие квоты оставили дырку, заполняем лучшим остатком — бюджет важнее.
    if len(selected) < budget:
        rest = sorted(work, key=lambda r: (-r["_order"], r["rel"].casefold()))
        for rec in rest:
            key = os.path.normcase(os.path.abspath(rec["path"]))
            if key in chosen:
                continue
            chosen.add(key)
            selected.append(rec)
            if len(selected) >= budget:
                break
    return selected[:budget]


def _files_project_map(records):
    """Короткая карта корня: масштаб, зоны, форматы и архитектурные опоры."""
    if not records:
        return None
    project = records[0]["project"]
    areas, exts, roles = {}, {}, {}
    fingerprint = []
    for rec in records:
        areas[rec["area"]] = areas.get(rec["area"], 0) + 1
        name = os.path.basename(rec["rel"])
        ext = os.path.splitext(name)[1].lower() or name.lower()
        exts[ext] = exts.get(ext, 0) + 1
        roles[rec["role"]] = roles.get(rec["role"], 0) + 1
        try:
            stat = os.stat(rec["path"])
            fingerprint.append(f"{rec['rel']}:{stat.st_size}:{stat.st_mtime_ns}")
        except OSError:
            fingerprint.append(rec["rel"])
    top_areas = sorted(areas.items(), key=lambda x: (-x[1], x[0].casefold()))[:10]
    top_exts = sorted(exts.items(), key=lambda x: (-x[1], x[0]))[:10]
    anchors = sorted(records, key=lambda r: (-r["score"], r["rel"].casefold()))[:12]
    context = "\n".join(
        [
            f"Проект: {project}. Пригодных файлов: {len(records)}.",
            "Зоны: " + ", ".join(f"{name} ({count})" for name, count in top_areas),
            "Форматы: " + ", ".join(f"{name} ({count})" for name, count in top_exts),
            "Роли: " + ", ".join(f"{name}={count}" for name, count in sorted(roles.items())),
            "Опорные файлы: " + ", ".join(rec["rel"] for rec in anchors),
            "Эта карта описывает весь корень; фрагменты ниже — углубление в выбранные файлы.",
        ]
    )[: config.FILES_CONTEXT_CHARS]
    digest = hashlib.sha1("\n".join(sorted(fingerprint)).encode(config.HTTP_CHARSET_UTF8)).hexdigest()[:16]
    return {
        "title": f"[КАРТА ПРОЕКТА] {project}",
        "context": context,
        "url": "",
        "id": "map:" + digest,
        "project": project,
        "project_map": True,
        "always_context": True,
    }


def _files(n, timeout, env):
    roots = list(env.get("files_paths") or [])
    if not roots:
        raise ValueError("files: no folders configured (env['files_paths'])")
    found = []                            # records: path/base/project/rel/area/role/score
    by_real_path = {}
    scanned = 0                           # предохранитель: не осматриваем больше FILES_MAX_SCAN файлов
    max_scan = _FILES_MAX_SCAN
    for root in roots:
        if scanned >= max_scan:
            break
        if os.path.isfile(root):
            cand = [(root, os.path.dirname(root))]
        elif os.path.isdir(root):
            cand = _files_walk(root)      # ленивый обход (обрывается при упоре в потолок)
        else:
            continue                      # путь не существует — молча пропускаем (не крашим)
        for p, base in cand:
            scanned += 1
            if scanned > max_scan:  # потолок файлов — дальше не идём (тик не виснет на диске)
                break
            if _files_is_candidate(p):     # секрет/не-текст/крупный — мимо (общий фильтр с probe_paths)
                rec = _files_record(p, base)
                real = os.path.normcase(os.path.realpath(p))
                old = by_real_path.get(real)
                # Пересекающиеся roots: оставляем самый конкретный (короче относительный путь).
                if old is None or len(rec["rel"]) < len(old["rel"]):
                    by_real_path[real] = rec
    found = list(by_real_path.values())
    if not found:
        raise ValueError("files: no readable text files in configured folders")
    max_items = _FILES_MAX_ITEMS
    take_n = min(max(1, int(n)), max_items)

    by_base = {}
    for rec in found:
        by_base.setdefault(os.path.normcase(os.path.abspath(rec["base"])), []).append(rec)
    maps = []
    if len(found) >= 8 and take_n >= 8:
        map_cap = min(_FILES_MAX_PROJECT_MAPS, max(1, take_n // 6))
        # Если все файлы и карты помещаются — ничего не жертвуем. Иначе карты входят в бюджет.
        available = max(0, take_n - min(len(found), take_n))
        want = min(len(by_base), map_cap)
        map_slots = min(want, available if available else want)
        for key in sorted(by_base, key=lambda k: _files_root_label(by_base[k][0]["base"]).casefold())[:map_slots]:
            project_map = _files_project_map(by_base[key])
            if project_map:
                maps.append(project_map)

    file_budget = min(len(found), max(1, take_n - len(maps)))
    picked = _files_select(found, file_budget)
    items = list(maps)
    for rec in picked:
        p, base, rel = rec["path"], rec["base"], rec["rel"]
        headline = _files_headline(p)
        titled_path = f"[{rec['project']}] {rel}"
        title = (f"{titled_path} — {headline}" if headline else titled_path)[: config.FILES_ITEM_TITLE_MAX_CHARS]
        context = _files_context(p, headline)
        item = {"title": title, "url": "", "id": _files_item_id(p, base, headline, context)}
        item["project"] = rec["project"]
        item["path"] = rel
        item["role"] = rec["role"]
        if rec["role"] in {"overview", "manifest", "entrypoint"}:
            item["always_context"] = True
        if context:
            item["context"] = context
        items.append(item)
    return items


def _self(n, timeout, env):
    """Собственный код kiborg как ОТДЕЛЬНЫЙ источник саморефлексии.

    Использует тот же безопасный и ограниченный обход, что ``files``, но не зависит
    от пользовательского списка папок. ``self_path`` подкладывает слой cyborg.
    Маркер ``kind`` позволяет генератору отличить самоанализ от обычного локального
    сырья, когда оба вида материалов смешаны в одном прогоне.
    """
    root = env.get("self_path")
    if not isinstance(root, (str, os.PathLike)) or not os.fspath(root).strip():
        raise ValueError("self: no kiborg project path configured (env['self_path'])")
    local_env = dict(env)
    local_env["files_paths"] = [os.fspath(root)]
    items = _files(n, timeout, local_env)
    for item in items:
        item["kind"] = "self_reflection"
        # Тот же файл в нейтральном files и в самоанализе — разные смысловые
        # материалы. Отдельный ID не даёт старому seen-items поглотить новый источник.
        item["id"] = "self:" + str(item.get("id") or "")
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
        max_scan = _FILES_MAX_SCAN
        for p, _base in entries:
            if scanned >= max_scan:
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
    "self": _self,
    "files": _files,
}


def run(inputs, env):
    env = env or {}
    n = int(env.get("n", 8))
    timeout = float(env.get("timeout", 8))
    sources = env.get("sources")
    if sources is not None and not sources:
        # ЯВНО пустой список (все ленты выключены в пульте И папок нет) — НЕ дефолтим на hn.
        # Контракт harvest._active_sources: пусто → не собираем, пульт предупреждает.
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
        # ни один источник не дал сырья -> честно пусто, без подмены демо-заголовками. Это
        # деградация, но не краш: НЕ ключ "error", чтобы сбор повторился на следующем тике.
        return {"items": [], "source": label, "degraded": True,
                "degraded_reason": "; ".join(errors) or "no items"}

    out = {"items": items, "source": label, "degraded": False}
    if errors:
        out["partial_errors"] = errors  # часть источников не ответила, но сырьё уже есть
    return out


if __name__ == "__main__":
    print(json.dumps(run({}, {"n": 5}), ensure_ascii=False, indent=2))
