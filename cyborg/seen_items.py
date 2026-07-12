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


def filter_fresh(items):
    """Возвращает items МИНУС уже виденные и отмечает возвращённые (с id) виденными.
    Items без id (никогда не должно случиться для наших источников, но на всякий) —
    всегда проходят: лучше лишний раз показать, чем молча потерять сырьё."""
    seen = load()
    original = set(seen)
    fresh = []
    for it in items:
        key = _item_key(it)
        if key is None or key not in original:
            fresh.append(it)
        if key is not None:
            seen.add(key)
    if seen != original:
        _save(seen)
    return fresh
