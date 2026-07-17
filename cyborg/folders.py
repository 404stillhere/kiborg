"""Папки-источник — какие локальные папки киборг читает как сырьё для идей.

Хранит список путей в data/folders.json:
    {"paths": ["M:/projects/kiborg", "C:/Users/User/notes"]}

Пусто = источник «files» выключен (киборг берёт идеи только из лент). Юзер правит список
в пульте мышкой (или прямо в файле). Смотрит на папки НЕЙТРАЛЬНО — как на чужой проект со
стороны, без «чини себя». Секреты и мусорные папки отсеивает сам орган (collect_source._files),
не этот модуль. Только stdlib (panel/serve.py импортит его, а он без venv)."""
import os

import _panel_config

DATA = _panel_config.data_dir_for(__file__)
PATH = os.path.join(DATA, "folders.json")

_MAX_PATHS = 40      # папок немного; больше — мусор/раздувание
_MAX_LEN = 400       # путь бывает длинным, но не полотно


def _clean_paths(seq):
    """Список путей → чистый: тримминг, снять кавычки, \\ → /, без хвостового /, без пустых
    и дублей (регистронезависимо), потолок по числу. Диск-корень «M:/» сохраняем как есть."""
    out, seen = [], set()
    for p in seq:
        if not isinstance(p, str):
            continue
        c = p.strip().strip('"').strip("'").replace("\\", "/")[:_MAX_LEN].rstrip("/")
        if c.endswith(":"):              # «M:» (из «M:/») — вернуть корень диска «M:/»
            c += "/"
        key = c.lower()
        if c and key not in seen:
            seen.add(key)
            out.append(c)
        if len(out) >= _MAX_PATHS:
            break
    return out


def load():
    """Список папок с диска. Нет файла / битый → пусто (источник выключен)."""
    d = _panel_config.load_obj(PATH)
    paths = d.get("paths")
    paths = _clean_paths(paths) if isinstance(paths, list) else []
    return {"paths": paths}


def current():
    """Только список путей (для env прогона). [] = источник-папка выключен."""
    return load()["paths"]


def save(paths):
    """Атомарно сохранить список папок. Чистка/дедуп/потолки — здесь."""
    clean = _clean_paths(paths) if isinstance(paths, list) else []
    _panel_config.atomic_save(PATH, {"paths": clean})
    return {"paths": clean}


if __name__ == "__main__":
    print("папки-источник:", load())
