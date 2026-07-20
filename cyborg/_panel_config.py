"""Общий скелет для пультовых конфиг-тумблеров (feeds/folders/direction/council_config).

Каждый из этих модулей хранит кусок состояния юзера в data/<name>.json и даёт три вызова:
load() / save() / <current>(). Каркас у всех дословно один и тот же (atomic save через
tmp+os.replace, load с защитой от битого/не-dict JSON, инициализация DATA/PATH от __file__).
Этот модуль — вынесенный общий хелпер, чтобы 4 копии скелета не дублировались (jscpd
ловил их как клоны). Индивидуальная логика (_clean / _clean_paths / пресеты / наборы
советников) остаётся в каждом модуле — тут только транспорт JSON <-> dict.

Только stdlib (panel/serve.py импортит эти модули без venv → и хелпер тоже stdlib-only).
"""

import json
import os


def data_dir_for(module_file):
    """Абсолютный путь к cyborg/data/ от пути к файлу модуля (cyborg/<x>.py → cyborg/data).
    Общая идиома `os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")`,
    вынесенная сюда, чтобы не повторять 4 раза."""
    return os.path.join(os.path.dirname(os.path.abspath(module_file)), "data")


def load_obj(path):
    """Прочитать JSON-объект с диска. Нет файла / битый / не-dict → {}. Атомарности не
    требует (чтение), но защиту от мусора даёт — все 4 модуля имели одинаковый try/except
    + isinstance(dict) guard. Возвращает dict (никогда не None и не другой тип)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def atomic_save(path, payload):
    """Атомарно записать JSON-объект: tmp-файл → json.dump → os.replace (обрыв записи не
    бьёт существующий набор). Создаёт родительский каталог при необходимости. Возвращает
    payload (для удобства chain). Общий хвост save() всех 4 модулей."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return payload
