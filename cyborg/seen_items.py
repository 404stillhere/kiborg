"""Трекер «уже видели» — ID СЫРЫХ items (заголовков/репо), а не сгенерированных идей.

Зачем отдельно от текстового дедупа ГОТОВЫХ идей (idea_engine/store.py, поле "seen" —
сигнатуры заголовков идей): тот дедупит уже ПОСЛЕ дорогого вызова ideate по ТЕКСТУ — если
LLM перефразирует старый заголовок чуть иначе, сигнатура может не поймать повтор, а деньги/
токены на генерацию уже потрачены. Этот модуль дедупит ДО ideate, по ID самого источника
(HN item id, reddit id, lobsters short_id, github owner/repo) — точнее и дешевле: не тратим
LLM на заголовок, который уже разбирали в прошлый раз.

Формат хранения (с 2026-07-21): dict[str, int] — ключ "source:id" → unix-ts ПОСЛЕДНЕГО
видения. Ts нужен для TTL: при каждом mark_seen файл заодно ЧИСТИТ себя от записей старше
TTL_DAYS (90) — иначе рос бы без огранички (263 записи за 2 месяца → десятки тысяч за год,
load() на каждом тике автосбора начал бы тормозить). Страховочный MAX_RECORDS (5000) — если
TTL не спасёт при массовом притоке, обрежем по самым свежим. Файл атомарен через .tmp+rename.

Стабилизация ключей: collect_source выдаёт files:* версионированный `f2:`-id из имени корня,
относительного пути и видимого заголовка. Он переживает перенос корня, различает одноимённые
файлы в разных папках и замечает изменение сырья, которое увидит LLM. Старые пути и хеши
basename изолируются под `legacy-`: потерянный каталог восстановить уже нельзя.
Для источников со стабильным id (hn/lobsters/gh_trending/reddit) — id как есть, без хеша.

Персист: cyborg/data/seen_items.json.
"""

import hashlib
import json
import os
import re
import sys
import time

# path-bootstrap: seen_items.py импортируется из run.py как скрипт (cyborg/ в path),
# из тестов как модуль (project-root в path), и из harvest_* подмодулей как сосед.
# Единый хак: гарантируем, что project-root на пути, чтобы `from cyborg import config` работал.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from cyborg import config

DATA = os.path.join(_HERE, "data")
PATH = os.path.join(DATA, config.SEEN_ITEMS_FILE)

TTL_DAYS = config.SEEN_ITEMS_TTL_DAYS
MAX_RECORDS = config.SEEN_ITEMS_MAX_RECORDS


_FILES_V2_PREFIX = "f2:"
_FILES_MAP_PREFIX = "map:"
_FILES_LEGACY_PREFIX = "legacy-"
_FILES_LEGACY_HASH_RE = re.compile(r"^[0-9a-f]{12}$")


def _legacy_files_token(value):
    """Старый files-id → отдельное legacy-пространство.

    Прошлая схема сжимала абсолютный путь до basename и уже потеряла каталог.
    Восстановить эту информацию нельзя; отделяем старые ключи от v2, чтобы они
    больше не подавляли сотни разных файлов с одним именем.
    """
    raw = str(value).replace("\\", "/")
    basename = raw.rsplit("/", 1)[-1]
    if _FILES_LEGACY_HASH_RE.match(basename):
        digest = basename
    else:
        digest = hashlib.sha1(basename.encode(config.HTTP_CHARSET_UTF8)).hexdigest()[: config.SEEN_ITEMS_HASH_LEN]
    return _FILES_LEGACY_PREFIX + digest


def _item_key(it):
    if not isinstance(it, dict):
        return None
    iid = it.get("id")
    if iid in (None, ""):
        return None  # без id дедуп невозможен — пропускаем как «всегда свежий», не теряем сырьё
    src = it.get("source", "?")
    # files: v2-id приходит от collect_source как хеш имени корня + относительного пути +
    # видимого headline. Не сжимаем его до basename: M:/projects содержит сотни main.py,
    # и старое сжатие теряло до четверти файлов. Старые пути/хеши держим отдельно в legacy,
    # чтобы новый точный id не наследовал их необратимые коллизии.
    if src == "files":
        raw = str(iid)
        iid = raw if raw.startswith((_FILES_V2_PREFIX, _FILES_MAP_PREFIX)) else _legacy_files_token(raw)
    return f"{src}:{iid}"


def _now():
    return int(time.time())


def _ttl_cutoff():
    return _now() - TTL_DAYS * config.SECONDS_PER_DAY


def _normalize_key(k):
    """Перевести старые files-ключи в явное legacy-пространство, v2 не трогать."""
    if isinstance(k, str) and k.startswith("files:"):
        rest = k[len("files:") :]
        if rest.startswith((_FILES_V2_PREFIX, _FILES_MAP_PREFIX, _FILES_LEGACY_PREFIX)):
            return k
        return "files:" + _legacy_files_token(rest)
    return k


def _migrate(raw):
    """Принять ЛЮБОЙ старый/новый формат → dict[str, int] в каноническом виде. Старый list[str]
    (до 2026-07-21, без ts) мигрируется: все ключи получают ts=сейчас (чтобы не потерять защиту —
    иначе при первом запуске с TTL весь архив разом стал бы «просроченным» и выкинулся), а
    files:*-ключи перехешируются до basename (см. _normalize_key). dict уже в новом формате —
    пропускаем как есть, НО files-ключи нормализуем (на случай, если в файле ещё живы старые
    записи с полными путями — двухформатовое состояние)."""
    now = _now()
    if isinstance(raw, dict):
        out = {}
        for k, v in raw.items():
            nk = _normalize_key(k)
            if isinstance(v, (int, float)) and v > 0:
                out[nk] = int(v)
            else:
                out[nk] = now  # мусорное значение ts — обновим на сейчас
        return out
    if isinstance(raw, list):
        return {_normalize_key(str(k)): now for k in raw if isinstance(k, str) and k}
    return {}


def load():
    """dict[str, int] (ключ → ts последнего видения). Пустой dict при отсутствии/битом файле.
    НЕ чистит TTL (read-only) — чистка только в _save (write-path). count_fresh/filter_fresh
    читают без мутации, TTL-уборка им не нужна — она и так случится при ближайшей записи."""
    try:
        with open(PATH, encoding=config.HTTP_CHARSET_UTF8) as f:
            return _migrate(json.load(f))
    except Exception:
        return {}


def _prune(seen):
    """Убрать просроченные (старше TTL_DAYS) и обрезать до MAX_RECORDS по свежим. Возвращает
    НОВЫЙ dict (не мутирует вход). Вызывается из _save перед персистом — файл сам себя чистит,
    без отдельного cron'а/процесса."""
    cutoff = _ttl_cutoff()
    live = {k: v for k, v in seen.items() if v >= cutoff}
    if len(live) > MAX_RECORDS:
        # оставляем самые свежие MAX_RECORDS (по ts desc); при равенстве ts — лексикальки
        live = dict(sorted(live.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_RECORDS])
    return live


def _save(seen):
    """Атомарный write-rename через .tmp. Перед персистом — TTL-чистка + cap (файл не растёт
    бесконтрольно, даже если mark_seen дёргают часто)."""
    os.makedirs(DATA, exist_ok=True)
    seen = _prune(seen)
    tmp = PATH + config.ATOMIC_TMP_SUFFIX
    with open(tmp, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
        json.dump(seen, f, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, PATH)


def count_fresh(items):
    """Дешёвый non-mutating подсчёт: сколько items ЕЩЁ не видели (для gate-проверки БЕЗ
    того, чтобы отмечать их виденными раньше времени — отметка идёт только в filter_fresh,
    когда items реально уходят на генерацию идей)."""
    seen = load()
    return sum(1 for it in items if _item_key(it) not in seen or _item_key(it) is None)


def filter_fresh(items, mark=True):
    """Возвращает items МИНУС уже виденные. По умолчанию (mark=True) СРАЗУ отмечает
    возвращенные (с id) виденными и персистит — прежнее поведение. mark=False: только
    фильтрует, файл НЕ трогает — пометку делает отдельный mark_seen ПОСЛЕ успешной генерации,
    чтобы транзиентная осечка ideate не сожгла сырьё безвозврата (см. wiring._run_ideate).
    Items без id (не должно случаться для наших источников, но на всякий) — всегда проходят:
    лучше лишний раз показать, чем молча потерять сырьё."""
    seen = load()
    original = dict(seen)
    fresh = []
    for it in items:
        key = _item_key(it)
        if key is None or key not in original:
            fresh.append(it)
        if mark and key is not None:
            seen[key] = _now()  # ОБНОВЛЯЕМ ts (повторное видение = «свежая» запись)
    if mark and seen != original:
        _save(seen)
    return fresh


def mark_seen(items):
    """Отметить items (с id) виденными и персистить. Вызывать ПОСЛЕ успешной генерации идей,
    чтобы транзиентный сбой ideate (осечка парса / обрыв сети → болванки) не сжёг сырьё:
    непомеченные посты пройдут filter_fresh на следующем тике и получат ещё один шанс.
    Побочно: _save чистит TTL/cap — файл сам себя обслуживает."""
    seen = load()
    original = dict(seen)
    now = _now()
    for it in items:
        key = _item_key(it)
        if key is not None:
            seen[key] = now
    if seen != original:
        _save(seen)


def _title_sig(t):
    """Нормализованная сигнатура заголовка для кросс-источникового дедупа: lower, только буквы/
    цифры, служебные знаки срезаны, пробелы схлопнуты. «SIMD Tricks!» и «simd tricks» → одна
    сигнатура. СТРОГАЯ (точное совпадение сигнатуры = дубль), не Jaccard — иначе «SIMD tricks»
    и «SIMD for collision» схлопнулись бы (это разные посты, похожие слова)."""
    return " ".join(re.findall(r"[a-zа-яё0-9]+", (t or "").lower()))


def cross_dedup(items):
    """Убрать кросс-источниковые дубли ВНУТРИ одного прогона (чистая функция, без персиста).

    Реальный кейс: один и тот же пост приходит с HN (item id) и Lobsters (short_id) → в
    seen_items это два разных ключа (hn:1 и lobsters:abc), оба проходят filter_fresh → LLM
    тратится на две похожие идеи. Здесь — убираем дубль ДО ideate, по нормализованному
    заголовку: первое вхождение выигрывает, мимо — дубли.

    СТРОГАЯ: только точное совпадение нормализованной сигнатуры (не Jaccard). «SIMD tricks» и
    «SIMD for collision» — РАЗНЫЕ посты, не схлопываются. Пустой title / без title — пропускаем
    как есть (не дедупим — лучше показать, чем потерять сырьё). Сохраняет порядок первого
    вхождения. Не-список → []. Не трогает seen_items.json (чистый read-only вычислитель)."""
    if not isinstance(items, list):
        return []
    out = []
    seen_sigs = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        sig = _title_sig(it.get("title"))
        # пустой title (только служебные слова/нет слов) — не дедупим, пропускаем как есть
        if not sig:
            out.append(it)
            continue
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        out.append(it)
    return out
