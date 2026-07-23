"""B1 council_weights — адаптивные веса советников (Feedback Cortex, уровень B).

Хранит {enabled, weights, updated_after} в data/council_weights.json:
    {"enabled": false, "weights": {"ask_llm": 0.39, "orchestra": 0.20, "rank_ideas": 0.41},
     "updated_after": 0}

По умолчанию enabled=false (канон mind.WEIGHTS неизменен, scoped rebind в wiring_council
НЕ активируется). Веса = DEFAULT_WEIGHTS (= mind.WEIGHTS на момент написания). Когда
Feedback Cortex (B4) накопит ≥20 триажей → processor обновит веса и поставит enabled=true.

API: load(), save(obj), is_enabled(), current_weights() — stdlib-only (панель читает без
venv, как council_config). Скелет — копия council_config.py + _panel_config (тот же
транспорт JSON, индивидуальная логика — merge partial weights с DEFAULT).

Scoped rebind в wiring_council (B2): обёртка читает current_weights() ТОЛЬКО когда
is_enabled(), и подменяет mind.WEIGHTS в try/finally вокруг deliberate. mind.py НЕ
трогается (FROZEN). test_mind.py НЕ ломается (там deliberate зовётся напрямую, минуя
обёртку → WEIGHTS остаётся каноническим).
"""

import os

import _panel_config

DATA = _panel_config.data_dir_for(__file__)
PATH = os.path.join(DATA, "council_weights.json")

ALL_ADVISORS = ["rank_ideas", "ask_llm", "orchestra"]
# Канон = mind.WEIGHTS (mind.py:33-37). Копия здесь — чтобы не плодить зависимость от
# замороженного модуля в обёртке. Если mind.WEIGHTS когда-то поменяют — синхронизировать.
DEFAULT_WEIGHTS = {"ask_llm": 0.39, "orchestra": 0.20, "rank_ideas": 0.41}


def load():
    """Прочитать конфиг. Битый/нет файла → безопасный дефолт (disabled, DEFAULT_WEIGHTS)."""
    d = _panel_config.load_obj(PATH)
    enabled = d.get("enabled")
    if not isinstance(enabled, bool):
        enabled = False
    weights = d.get("weights")
    if not isinstance(weights, dict):
        weights = dict(DEFAULT_WEIGHTS)
    return {"enabled": enabled, "weights": _merge_defaults(weights), "updated_after": d.get("updated_after", 0)}


def _merge_defaults(weights):
    """Слить partial weights с DEFAULT: только известные советники, неизвестные — дефолт."""
    out = {}
    for adv in ALL_ADVISORS:
        v = weights.get(adv)
        if isinstance(v, (int, float)):
            out[adv] = v
        else:
            out[adv] = DEFAULT_WEIGHTS[adv]
    return out


def is_enabled():
    """Активирован ли адаптивный режим весов (Feedback Cortex). False = канон mind.WEIGHTS."""
    return bool(load()["enabled"])


def current_weights():
    """Текущие веса (DEFAULT если disabled). Формат — как mind.WEIGHTS: {name: weight}."""
    return load()["weights"]


def save(obj):
    """Атомарно сохранить конфиг. obj = {enabled, weights, updated_after?}."""
    if not isinstance(obj, dict):
        obj = {}
    enabled = obj.get("enabled")
    if not isinstance(enabled, bool):
        enabled = False
    weights = obj.get("weights")
    if not isinstance(weights, dict):
        weights = dict(DEFAULT_WEIGHTS)
    payload = {
        "enabled": enabled,
        "weights": _merge_defaults(weights),
        "updated_after": obj.get("updated_after", 0),
    }
    _panel_config.atomic_save(PATH, payload)
    return payload
