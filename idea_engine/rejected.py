"""Отклонённые идеи — что юзер пометил «мусор». Храним СУТЬ (заголовок + почему + когда) в
data/rejected.json, чтобы киборг УЧИЛСЯ на отказах, а не только дедупил заголовки.

    {"rejected": [{"title": "...", "why": "...", "ts": "2026-07-18 21:00:00"}, ...]}

Пишется при триаже «мусор» (idea_engine/run.py убирает идею из списков и кладёт её суть сюда).
Читается генератором ideate («не придумывай похожее») и судьёй rank_ideas («похожее — ниже»)
через env["rejected"] (список последних заголовков). Совет над готовыми уже не тратится на то,
что ты забраковал. Только stdlib (панель/оболочка импортят без venv)."""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cyborg"))

import config

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATH = os.path.join(DATA, config.REJECTED_FILE)

_MAX = config.REJECTED_MAX_ITEMS  # помним последние N отклонённых (файл не растёт бесконечно)
_CONTEXT_N = config.REJECTED_CONTEXT_N  # сколько подавать генератору/судье как «не повторяй» (промпт не раздуть)


def _load():
    try:
        with open(PATH, encoding=config.HTTP_CHARSET_UTF8) as f:
            d = json.load(f)
        r = d.get(config.REJECTED_KEY)
        return r if isinstance(r, list) else []
    except Exception:
        return []


def _save(items):
    """Атомарно: во временный файл + os.replace — обрыв записи не обрежет rejected.json.
    tmp с pid: файл могут писать разные процессы (триаж-спавн пульта), уникальное имя снимает гонку."""
    os.makedirs(DATA, exist_ok=True)
    tmp = f"{PATH}.{config.ATOMIC_TMP_PID_SUFFIX.format(pid=os.getpid())}"
    try:
        with open(tmp, "w", encoding=config.HTTP_CHARSET_UTF8) as f:
            json.dump({config.REJECTED_KEY: items[-_MAX:]}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def add(title, why=""):
    """Записать отклонённую идею. Дедуп по заголовку (регистронезависимо) — не копим повторы."""
    title = (title or "").strip()
    if not title:
        return
    items = _load()
    key = title.lower()
    if any((it.get("title", "").strip().lower() == key) for it in items):
        return
    items.append(
        {
            "title": title[: config.REJECTED_TITLE_MAX_CHARS],
            "why": (why or "")[: config.REJECTED_WHY_MAX_CHARS],
            "ts": datetime.datetime.now().strftime(config.DATETIME_FMT),
        }
    )
    _save(items)


def recent(n=_CONTEXT_N):
    """Последние N заголовков отклонённых — для env['rejected'] (контекст генератору/судье)."""
    return [it.get("title", "") for it in _load()[-n:] if it.get("title")]


def count():
    """Сколько идей отклонено (для пульта)."""
    return len(_load())


def load():
    """Полный список для пульта, если понадобится показать."""
    return {config.REJECTED_KEY: _load()}


if __name__ == "__main__":
    print("отклонённых:", count(), "· последние:", recent(5))
