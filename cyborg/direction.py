"""Направление идей — руль генератора: тема, в сторону которой киборг придумывает.

Хранит текущий выбор + редактируемый список пресетов в data/direction.json:
    {"current": "дев-тулзы", "presets": ["дев-тулзы", "железки", ...]}

current="" (пусто) = БЕЗ направления — генерим как раньше, из того что в лентах.
Юзер выбирает пресет ИЛИ вписывает своё (панель); руль долетает до ideate/rank через env.
Только stdlib (panel/serve.py импортит этот модуль, а он без venv)."""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATH = os.path.join(DATA, "direction.json")

# стартовый набор — юзер правит на панели (это лишь дефолт, когда файла ещё нет)
_DEFAULT_PRESETS = ["дев-тулзы", "железки", "для родителей", "игры", "здоровье", "бизнес"]
_MAX_LEN = 120        # руль — короткая тема, не полотно
_MAX_PRESETS = 40     # список кнопок; больше — мусор


def _clean(s):
    return str(s).strip()[:_MAX_LEN] if isinstance(s, str) or s is not None else ""


def _clean_presets(seq):
    out, seen = [], set()
    for p in seq:
        c = _clean(p)
        if c and c.lower() not in seen:      # без пустых и без дублей (регистронезависимо)
            seen.add(c.lower())
            out.append(c)
        if len(out) >= _MAX_PRESETS:
            break
    return out


def load():
    """Текущее состояние руля с диска. Нет файла / битый → дефолт (current='', пресеты-семя)."""
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    current = _clean(d.get("current", ""))
    presets = d.get("presets")
    presets = _clean_presets(presets) if isinstance(presets, list) else list(_DEFAULT_PRESETS)
    return {"current": current, "presets": presets}


def current():
    """Только активное направление (для env прогона). '' = без направления."""
    return load()["current"]


def save(current=None, presets=None):
    """Атомарно сохранить руль. Меняем только переданное (None = не трогать поле)."""
    cur = load()
    if current is not None:
        cur["current"] = _clean(current)
    if presets is not None and isinstance(presets, list):
        cur["presets"] = _clean_presets(presets)
    os.makedirs(DATA, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)           # атомарно: обрыв записи не бьёт существующий выбор
    return cur


if __name__ == "__main__":
    print("текущий руль:", load())
