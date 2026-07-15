"""Конфигурация совета советников — какие советники активны при отборе (рубильники в пульте).

Хранит набор ВКЛЮЧЁННЫХ советников в data/council.json:
    {"enabled": ["rank_ideas", "ask_llm", "orchestra"]}

Доступные советники:
    rank_ideas (арбитр)
    ask_llm (интуиция)
    orchestra (оркестр)

Только stdlib (panel/serve.py импортит его без venv).
"""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PATH = os.path.join(DATA, "council.json")

ALL_ADVISORS = ["rank_ideas", "ask_llm", "orchestra"]
DEFAULT_ENABLED = ["rank_ideas", "ask_llm", "orchestra"]


def load():
    """{"all": ALL_ADVISORS, "enabled": [...]} с диска."""
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    enabled = d.get("enabled")
    if not isinstance(enabled, list):
        enabled = list(DEFAULT_ENABLED)
    enabled = [x for x in ALL_ADVISORS if x in enabled]
    return {"all": list(ALL_ADVISORS), "enabled": enabled}


def is_enabled(name):
    """Включен ли советник по имени."""
    return name in load()["enabled"]


def save(names):
    """Атомарно сохранить на диске набор включённых советников."""
    if not isinstance(names, list):
        names = []
    clean = [x for x in ALL_ADVISORS if x in names]
    os.makedirs(DATA, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"enabled": clean}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
    return {"all": list(ALL_ADVISORS), "enabled": clean}
