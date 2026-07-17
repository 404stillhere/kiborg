"""Направление идей — руль генератора: тема, в сторону которой киборг придумывает.

Хранит текущий выбор + редактируемый список пресетов в data/direction.json:
    {"current": "дев-тулзы", "presets": ["дев-тулзы", "железки", ...]}

current="" (пусто) = БЕЗ направления — генерим как раньше, из того что в лентах.
Юзер выбирает пресет ИЛИ вписывает своё (панель); руль долетает до ideate/rank через env.
Только stdlib (panel/serve.py импортит этот модуль, а он без venv)."""
import os

import _panel_config

DATA = _panel_config.data_dir_for(__file__)
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
    d = _panel_config.load_obj(PATH)
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
    _panel_config.atomic_save(PATH, cur)
    return cur


if __name__ == "__main__":
    print("текущий руль:", load())
