"""Разобранные идеи — «взятые» (taken) и «отложенные» (later). Два отдельных master-файла
рядом с state.json и rejected.json.

    data/taken.json  — {"taken":  [{...полная идея..., "triaged_ts": "YYYY-MM-DD HH:MM:SS"}, ...]}
    data/later.json  — {"later":  [{...полная идея..., "triaged_ts": "YYYY-MM-DD HH:MM:SS"}, ...]}

Пишется при триаже (idea_engine/run.py: take/later — идея уходит из state.json и ложится
сюда целиком, с меткой времени действия). Читается пультом (panel/serve.py: _read_inbox
отдаёт taken/later в /api/state → UI рисует «Разобранные»).

Отличие от rejected.py: храним ПОЛНУЮ идею (id/title/why/score/born_tick/…), без дедупа и
без потолка — взятые/отложенные идеи не должны теряться. Только stdlib (пульт импортит без
venv). Атомарность — tmp-файл + os.replace (как в rejected.py и store.py)."""

import datetime
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TAKEN_PATH = os.path.join(DATA, "taken.json")
LATER_PATH = os.path.join(DATA, "later.json")


def _load(path):
    """Прочитать {<key>: [...]} с диска. Нет файла / битый / не-dict → []."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    # принимаем оба ключа (taken/later) — caller передаёт путь, формат детерминирован
    for k in ("taken", "later"):
        v = d.get(k)
        if isinstance(v, list):
            return v
    return []


def _save(path, items, key):
    """Атомарно записать {key: items}: tmp-файл + os.replace (обрыв записи не бьёт файл).
    tmp с pid: файл могут писать разные процессы (триаж-спавн пульта), уникальное имя
    снимает гонку за общий .tmp (как в rejected.py / store.py)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({key: items}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _key_for(path):
    """Имя ключа ('taken' / 'later') по пути файла — для atomic_save."""
    base = os.path.basename(path)
    return "taken" if base.startswith("taken") else "later"


def add(path, idea):
    """Добавить ПОЛНУЮ идею в файл (taken.json или later.json) с меткой времени действия.
    Идемпотентно по id: если идея с тем id уже в файле — не дублируем (защита от повторного
    триажа одной идеи, напр. при гонке двух процессов). Возвращает обновлённый список."""
    key = _key_for(path)
    items = _load(path)
    iid = idea.get("id")
    if iid is not None and any(it.get("id") == iid for it in items):
        return items  # уже разобрана — не дублируем
    idea = dict(idea)  # не мутируем вызывающий словарь
    idea.setdefault("triaged_ts", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    items.append(idea)
    _save(path, items, key)
    return items


def load(path):
    """Полный список разобранных идей из файла — для пульта ({"taken": [...]}/{...}).
    Возвращает {<key>: [...]}, пустой каркас при отсутствии/битом файле."""
    key = _key_for(path)
    return {key: _load(path)}


def count(path):
    """Сколько идей в файле."""
    return len(_load(path))


if __name__ == "__main__":
    print("taken:", count(TAKEN_PATH), "· later:", count(LATER_PATH))
