"""Параметры генерации идей — настраиваемые юзером через пульт (drawer «Настройки»).

Хранит в data/genparams.json:
    {"gen_k": 8, "rank_keep": 3, "source_n": 105, "read_min_score": 8.0, "keep_min_score": 0.6}

Поток (как у direction/feeds/folders/council_config):
    UI → POST /api/genparams → save → data/genparams.json
    → harvest_env._source_env() читает → env прогона
    → wiring_ideate/wiring_council читают env.get(...)
Подхватывается ОБЕИМ кнопками (ручной run.py + автосбор harvest_runner), т.к. оба идут
через _source_env() — единый источник.

Параметры (выбраны по критерию «юзер видит эффект». Внутренние вроде _MMR_LAMBDA,
_PROV_THRESHOLD, Jaccard-дедупа — НЕ выносятся: юзеру непонятны, вред от ручной правки).

    gen_k          — сколько идей LLM генерит за прогон (дефолт 8, было хардкод в wiring_ideate)
    rank_keep      — сколько выживает после совета (дефолт 3, было хардкод в wiring_council)
    source_n       — сколько сырья собирается (дефолт 105, было harvest.SOURCE_N из config)
    read_min_score — порог читаемости: ниже переписывается (дефолт 8.0, уже env.get в wiring_council)
    keep_min_score — ниже какого балла совета идея не проходит (дефолт 0.6, было _KEEP_MIN_SCORE)

Только stdlib (panel/serve.py импортит без venv, как direction/council_config).
"""

import math
import os

import _panel_config

DATA = _panel_config.data_dir_for(__file__)
PATH = os.path.join(DATA, "genparams.json")

# (min, max, default, is_float) — для clamp при load и как метаданные для UI
# Диапазоны выбраны осмысленно: gen_k 2..16 (меньше 2 — нет выбора, больше 16 — размытие/долго);
# rank_keep 1..8 (минимум 1 идея, потолок = gen_k); source_n 8..300 (меньше 8 — пусто, больше 300 —
# таймаут/лимиты лент); read_min_score 0..10 (шкала оценки); keep_min_score 0..1 (нормированный балл совета).
PARAMS = {
    "gen_k": (2, 16, 8, False),
    "rank_keep": (1, 8, 3, False),
    "source_n": (8, 300, 105, False),
    "read_min_score": (0.0, 10.0, 8.0, True),
    "keep_min_score": (0.0, 1.0, 0.6, True),
}


def defaults():
    """Значения по умолчанию (кнопка «↺ сброс» в UI возвращает именно это)."""
    return {k: spec[2] for k, spec in PARAMS.items()}


def _clamp(name, val):
    """Привести значение в диапазон [min, max]. Невалидное → default (не падаем)."""
    lo, hi, dflt, is_float = PARAMS[name]
    if isinstance(val, bool):
        return dflt
    try:
        val = float(val) if is_float else int(val)
    except (TypeError, ValueError):
        return dflt
    if not math.isfinite(float(val)):
        return dflt
    return max(lo, min(hi, val))


def _normalize(values):
    """Полный валидный набор + межпараметрный инвариант rank_keep <= gen_k."""
    values = values if isinstance(values, dict) else {}
    out = {k: _clamp(k, values.get(k, spec[2])) for k, spec in PARAMS.items()}
    out["rank_keep"] = min(out["rank_keep"], out["gen_k"])
    return out


def load():
    """С диска с clamping по диапазонам. Нет файла / битый / не-dict → все дефолты.

    Read ВСЕГДА возвращает полный набор ключей (UI строит инпуты по этому контракту).
    Значения на диске могут быть вне диапазона (правили руками / старая версия) — clamp."""
    return _normalize(_panel_config.load_obj(PATH))


def save(updates):
    """Атомарно сохранить частичное обновление (только известные ключи, clamp).

    updates — dict с любым подмножеством ключей из PARAMS. Неизвестные ключи игнорируются
    (forward-compat: UI старой версии может слать устаревшие поля — не роняем save)."""
    cur = load()
    updates = updates if isinstance(updates, dict) else {}
    for k, v in updates.items():
        if k in PARAMS:
            cur[k] = _clamp(k, v)
    cur = _normalize(cur)
    _panel_config.atomic_save(PATH, cur)
    return cur


def reset():
    """Сброс к дефолтам (кнопка «↺ сброс» в UI). Перезаписывает файл полностью."""
    d = defaults()
    _panel_config.atomic_save(PATH, d)
    return d


def meta():
    """Метаданные для UI: для каждого параметра — min/max/default/is_float + текущее value.

    UI (app.js._renderGenparams) строит инпуты по этим данным: range min/max/step/value.
    keep_min_score в env хранится 0..1, в UI показывается 0..10 (×10) — пересчёт в app.js."""
    cur = load()
    params = {}
    for k, s in PARAMS.items():
        max_value = min(s[1], cur["gen_k"]) if k == "rank_keep" else s[1]
        params[k] = {
            "min": s[0],
            "max": max_value,
            "default": s[2],
            "is_float": s[3],
            "value": cur[k],
        }
    return {"params": params}


if __name__ == "__main__":
    print("параметры генерации:", load())
