"""Трекер «уже видели» — ID СЫРЫХ items (заголовков/репо), а не сгенерированных идей.

Зачем отдельно от stash.py: stash дедупит ГОТОВЫЕ идеи по ТЕКСТУ (после дорогого вызова
ideate) — если LLM перефразирует старый заголовок чуть иначе, Jaccard может не поймать
повтор, и деньги/токены на генерацию уже потрачены. Этот модуль дедупит ДО ideate, по ID
самого источника (HN item id, reddit id, lobsters short_id, github owner/repo) — точнее и
дешевле: не тратим LLM на заголовок, который уже разбирали в прошлый раз.

Персист: cyborg/data/seen_items.json — плоский список ключей "source:id".
"""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATH = os.path.join(DATA, "seen_items.json")


def _item_key(it):
    if not isinstance(it, dict):
        return None
    iid = it.get("id")
    if iid in (None, ""):
        return None  # без id дедуп невозможен — пропускаем как «всегда свежий», не теряем сырьё
    return f"{it.get('source', '?')}:{iid}"


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save(seen):
    os.makedirs(DATA, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False)
    os.replace(tmp, PATH)


def count_fresh(items):
    """Дешёвый non-mutating подсчёт: сколько items ЕЩЁ не видели (для gate-проверки БЕЗ
    того, чтобы отмечать их виденными раньше времени — отметка идёт только в filter_fresh,
    когда items реально уходят на генерацию идей)."""
    seen = load()
    return sum(1 for it in items if _item_key(it) not in seen or _item_key(it) is None)


def filter_fresh(items, mark=True):
    """Возвращает items МИНУС уже виденные. По умолчанию (mark=True) СРАЗУ отмечает
    возвращённые (с id) виденными и персистит — прежнее поведение. mark=False: только
    фильтрует, файл НЕ трогает — пометку делает отдельный mark_seen ПОСЛЕ успешной генерации,
    чтобы транзиентная осечка ideate не сожгла сырьё безвозвратно (см. wiring._run_ideate).
    Items без id (не должно случаться для наших источников, но на всякий) — всегда проходят:
    лучше лишний раз показать, чем молча потерять сырьё."""
    seen = load()
    original = set(seen)
    fresh = []
    for it in items:
        key = _item_key(it)
        if key is None or key not in original:
            fresh.append(it)
        if mark and key is not None:
            seen.add(key)
    if mark and seen != original:
        _save(seen)
    return fresh


def mark_seen(items):
    """Отметить items (с id) виденными и персистить. Вызывать ПОСЛЕ успешной генерации идей,
    чтобы транзиентный сбой ideate (осечка парса / обрыв сети → болванки) не сжёг сырьё:
    непомеченные посты пройдут filter_fresh на следующем тике и получат ещё один шанс."""
    seen = load()
    original = set(seen)
    for it in items:
        key = _item_key(it)
        if key is not None:
            seen.add(key)
    if seen != original:
        _save(seen)
