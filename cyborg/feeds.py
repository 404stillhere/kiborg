"""Ленты-источник — какие публичные ленты киборг обходит за идеями (тумблеры в пульте).

Хранит набор ВКЛЮЧЁННЫХ лент в data/feeds.json:
    {"enabled": ["telegram", "hn"]}

Доступные ленты — ALL_FEEDS (5 публичных). Папки («files») сюда НЕ входят: у них
свой контрол — они включаются фактом наличия папок (см. folders.py). Нет файла /
битый / нет ключа "enabled" → дефолт DEFAULT_FEEDS (как было захардкожено в
harvest.SOURCES: только telegram, лично курированный юзером). Пустой список —
валиден (юзер выключил все ленты; идеи тогда только из папок, если заданы).

Юзер щёлкает тумблеры в пульте. Только stdlib (panel/serve.py импортит его без venv)."""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATH = os.path.join(DATA, "feeds.json")

# Порядок = порядок показа тумблеров в пульте. Совпадает с collect_source._SOURCES
# минус 'files' (у папок свой блок). Меняется вместе с составом источников органа.
ALL_FEEDS = ["hn", "reddit", "lobsters", "gh_trending", "telegram"]
DEFAULT_FEEDS = ["telegram"]   # дефолт при отсутствии файла = прежний harvest.SOURCES


def _clean(seq):
    """Список имён лент → канонический: только известные (ALL_FEEDS), без дублей, в
    порядке ALL_FEEDS. Не список → None (сигнал «нет валидного набора» для load)."""
    if not isinstance(seq, list):
        return None
    picked = {s for s in seq if isinstance(s, str) and s in ALL_FEEDS}
    return [f for f in ALL_FEEDS if f in picked]


def load():
    """{"all": ALL_FEEDS, "enabled": [...]} с диска. Нет файла / битый / нет ключа →
    enabled = DEFAULT_FEEDS. Пустой сохранённый список остаётся пустым (это выбор юзера)."""
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    enabled = _clean(d.get("enabled"))
    if enabled is None:                       # ключа нет / битый тип → дефолт (не пусто)
        enabled = list(DEFAULT_FEEDS)
    return {"all": list(ALL_FEEDS), "enabled": enabled}


def enabled():
    """Только список включённых лент (для env прогона). [] = все ленты выключены."""
    return load()["enabled"]


def save(names):
    """Атомарно сохранить набор включённых лент. Чистка/дедуп/канон-порядок — здесь.
    Не список → пустой набор (все выключены)."""
    clean = _clean(names)
    if clean is None:
        clean = []
    os.makedirs(DATA, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"enabled": clean}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)                     # атомарно: обрыв записи не бьёт существующий набор
    return {"all": list(ALL_FEEDS), "enabled": clean}


if __name__ == "__main__":
    print("ленты-источник:", load())
