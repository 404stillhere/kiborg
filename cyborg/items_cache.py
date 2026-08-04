"""A6 cache_check — короткий фильтр повторов автосбора, TTL 30 мин.

Автосбор гоняет каждые ~30 мин. Если item уже пришёл в одном из последних 3 прогонов,
повторно его генератору не отдавать — экономим LLM-вызовы на заголовках, которые юзер
уже видел (в виде идеи или stub). Ручной run.py НЕ фильтрует (решение юзера — жмёшь
кнопку «Принеси идеи», хочешь идей сейчас, даже если посты мелькали: wiring_ideate.py
не ставит filter_seen_items, мы тут ему не конкуренты — это ОТДЕЛЬНЫЙ слой по title).

Ленты сравниваются по title (как раньше). Локальные файлы — по source+id, потому что
их id включает видимый context: изменение кода при прежнем заголовке обязано пройти.
always_context (карта проекта/архитектурные опоры) не отрезается — она нужна свежим
файлам для понимания целого проекта.

Структура файла data/items_cache.json:
    {"runs": [{"ts": <unix>, "titles": {"<sha256>": "<title>"}, ...}, ...]}

Хранятся последние MAX_RUNS прогонов. TTL — отдельно: даже в пределах 3 прогонов,
запись старше TTL_SEC считается протухшей (лента могла обновиться смыслом).

Только stdlib (panel читает конфиги без venv; хотя фильтр живёт в cyborg/, держим
транспорт stdlib-only — прецедент: council_config.py, _panel_config.atomic_save).
"""

import hashlib
import json
import os
import sys
import time

# path-bootstrap: items_cache.py импортируется из run.py как скрипт (cyborg/ в path),
# из тестов как модуль (project-root в path), и из harvest_* подмодулей как сосед.
# Единый хак: гарантируем, что project-root на пути, чтобы `from cyborg import config` работал.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from cyborg import config

MAX_RUNS = config.ITEMS_CACHE_MAX_RUNS
TTL_SEC = config.ITEMS_CACHE_TTL_SEC

DATA = os.path.join(_HERE, "data")
PATH = os.path.join(DATA, "items_cache.json")


def _sha256(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _item_hash(item):
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    iid = item.get("id")
    source = str(item.get("source") or "")
    iid_text = str(iid or "")
    if source in {"files", "self"} or iid_text.startswith(("f2:", "self:f2:", "map:", "self:map:")):
        return _sha256("id:" + source + ":" + iid_text)
    return _sha256(title)  # совместимо с уже записанным title-кэшем лент


def _load():
    """Прочитать кэш с диска. Битый/отсутствующий → {'runs': []} (всё свежее)."""
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {"runs": []}
    if not isinstance(d, dict):
        return {"runs": []}
    runs = d.get("runs")
    if not isinstance(runs, list):
        return {"runs": []}
    return {"runs": [r for r in runs if isinstance(r, dict)]}


def _atomic_write(path, text):
    """Атомарная запись (tmp + os.replace) — образец harvest_gate._atomic_write."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + config.ATOMIC_TMP_SUFFIX
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _save(cache):
    _atomic_write(PATH, json.dumps(cache, ensure_ascii=False))


def _known_titles(cache, now=None):
    """Множество SHA256 известных заголовков: объединение по рунам, не протухшим по TTL."""
    now = now if now is not None else time.time()
    known = set()
    for run in cache.get("runs", []):
        ts = run.get("ts")
        titles = run.get("titles")
        if not isinstance(ts, (int, float)) or not isinstance(titles, dict):
            continue
        if now - ts > TTL_SEC:  # протух — не считаем известным
            continue
        known.update(titles.keys())
    return known


def filter_fresh(items):
    """Вернуть items, чья сигнатура НЕ в кэше. Битый кэш / нет title → проходит.

    ПАССИВНЫЙ фильтр: только читает кэш, НЕ метит. mark_seen зовётся обёрткой ПОСЛЕ
    успешной генерации (по образцу seen_items.mark_seen в wiring_ideate) — иначе сбой
    генератора «сожжёт» заголовки, которые мы больше не предложим.
    """
    if not items:
        return []
    cache = _load()
    known = _known_titles(cache)
    out = []
    for it in items:
        if not isinstance(it, dict):
            out.append(it)  # не словарь — не можем сравнить, пропускаем как есть
            continue
        if it.get("always_context"):
            out.append(it)
            continue
        title = it.get("title")
        if not isinstance(title, str) or not title.strip():
            out.append(it)  # нет title — не фильтруем
            continue
        if _item_hash(it) in known:
            continue  # уже видели в свежем прогоне — отрезаем
        out.append(it)
    return out


def mark_seen(items):
    """Записать title items как «виденные в этом прогоне». Новый рун с now, ротация MAX_RUNS."""
    if not items:
        return
    titles = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        title = it.get("title")
        key = _item_hash(it)
        if key and isinstance(title, str) and title.strip():
            titles[key] = title
    if not titles:
        return
    cache = _load()
    runs = cache.get("runs", [])
    runs.append({"ts": time.time(), "titles": titles})
    runs = runs[-MAX_RUNS:]  # держим только последние MAX_RUNS прогонов
    cache["runs"] = runs
    _save(cache)


def _age_out_backdate(seconds):
    """ТЕСТ-хелпер: сдвинуть ts всех рунов назад на `seconds` (имитация протухания TTL).
    Только для test_items_cache — прод NEVER не зовёт."""
    cache = _load()
    delta = time.time() - seconds
    for run in cache.get("runs", []):
        if isinstance(run.get("ts"), (int, float)):
            run["ts"] = delta
    _save(cache)
