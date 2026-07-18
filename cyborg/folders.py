"""Папки-источник — какие локальные папки киборг читает как сырьё для идей.

Хранит список папок в data/folders.json, у КАЖДОЙ свой тумблер вкл/выкл:
    {"folders": [{"path": "M:/projects/kiborg", "on": true},
                 {"path": "C:/Users/User/notes", "on": false}]}

Включённых папок нет (или список пуст) = источник «files» выключен (киборг берёт идеи только
из лент). Юзер правит список в пульте мышкой: добавить путь, тумблер вкл/выкл у каждой,
удалить. Смотрит на папки НЕЙТРАЛЬНО — как на чужой проект со стороны, без «чини себя».
Секреты и мусорные папки отсеивает сам орган (collect_source._files), не этот модуль.

Обратная совместимость: старый формат {"paths": [...]} читается как список ВКЛючённых папок.
Только stdlib (panel/serve.py импортит его, а он без venv)."""
import os

import _panel_config

DATA = _panel_config.data_dir_for(__file__)
PATH = os.path.join(DATA, "folders.json")

_MAX_PATHS = 40      # папок немного; больше — мусор/раздувание
_MAX_LEN = 400       # путь бывает длинным, но не полотно


def _clean_path(p):
    """Один путь → нормализованный: тримминг, снять кавычки, \\ → /, без хвостового /, потолок
    по длине. Диск-корень «M:/» сохраняем как есть. Не строка / пусто → None."""
    if not isinstance(p, str):
        return None
    c = p.strip().strip('"').strip("'").replace("\\", "/")[:_MAX_LEN].rstrip("/")
    if c.endswith(":"):              # «M:» (из «M:/») — вернуть корень диска «M:/»
        c += "/"
    return c or None


def _clean(items):
    """Список папок → чистый [{"path","on"}]. Элемент: строка (→ on=True) или {path, on}.
    Нормализация пути, дедуп по пути (регистронезависимо, первое вхождение выигрывает — его
    тумблер и остаётся), потолок по числу."""
    out, seen = [], set()
    for it in items:
        if isinstance(it, str):
            path, on = it, True
        elif isinstance(it, dict):
            path, on = it.get("path"), it.get("on", True)
        else:
            continue
        c = _clean_path(path)
        if not c:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": c, "on": bool(on)})
        if len(out) >= _MAX_PATHS:
            break
    return out


def _read():
    """Список папок с диска [{"path","on"}]. Новый формат "folders" или старый плоский
    "paths" (все включены). Нет файла / битый → []."""
    d = _panel_config.load_obj(PATH)
    items = d.get("folders")
    if isinstance(items, list):
        return _clean(items)
    legacy = d.get("paths")           # обратная совместимость: старый плоский список = все вкл
    if isinstance(legacy, list):
        return _clean(legacy)
    return []


def load():
    """{"folders": [{"path","on"}], "paths": [включённые пути]} — полный список для пульта +
    только ВКЛючённые пути (для прогона). Нет папок / все выкл → paths пуст (источник выключен)."""
    fs = _read()
    return {"folders": fs, "paths": [f["path"] for f in fs if f["on"]]}


def current():
    """Только ВКЛючённые пути (для env прогона). [] = источник-папка выключен
    (папок нет ИЛИ все выключены тумблером)."""
    return load()["paths"]


def all_paths():
    """Все пути (вкл + выкл) — для пробы в пульте: счётчик файлов виден и у выключенной папки."""
    return [f["path"] for f in _read()]


def save(items):
    """Атомарно сохранить список папок. items: список строк или {path,on}. Чистка/дедуп/
    потолки — здесь. Не список → пусто (все убраны). Возвращает как load()."""
    fs = _clean(items) if isinstance(items, list) else []
    _panel_config.atomic_save(PATH, {"folders": fs})
    return {"folders": fs, "paths": [f["path"] for f in fs if f["on"]]}


if __name__ == "__main__":
    print("папки-источник:", load())
