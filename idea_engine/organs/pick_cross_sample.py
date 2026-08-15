"""Орган отбора: РОВНО по одному материалу с каждого источника, детерминированно по seed.

Контракт: run(inputs, env) -> dict. Чистая функция — случайность инжектится через
inputs["seed"] (генерит обёртка fuse_mode, значение пишется в карточку), а НЕ берётся
из глобального random: орган остаётся воспроизводимым, тесты — без monkeypatch.
Ключ/сеть орган сам НЕ трогает (LLM здесь нет вообще — только отбор).

Ключ материала = «source:id» — ТОТ ЖЕ формат, что у seen_items._item_key: обёртка
кормит used_ids прямо из seen_items.load().keys() без перекодирования, а combo_hash
fuse_ideas считает по той же паре.

inputs:
    items            : list[dict] {title, url, id, source} (после scrub/cross-dedup wiring_collect)
    seed             : int  ОБЯЗАТЕЛЕН (ValueError без него — чистота контракта органа)
    min_sources      : int = 2     сколько ЯДЕРНЫХ источников нужно минимум
    oversample       : int = 1     кандидатов на источник (2 = «лучший из пары» выберет LLM в слиянии)
    aux_sources      : list[str]   служебные источники, НЕ считающиеся «далёким доменом» (дефолт ["self"])
    include_aux      : bool=False  брать ли aux-материалы в выборку. По умолчанию НЕТ: живые
                                   прогоны 2026-08-15 показали, что LLM сам их выбрасывает из
                                   fusion (карточка «kiborg-тест-файл + ленты» разваливается),
                                   а вратарь «все источники обязательны» убивал за это годные
                                   карточки. Aux остаётся только порогом «не спасает минимум».
    used_ids         : list/set    «source:id» уже участвовавших материалов — мягко избегаем, не запрещаем
    sources_expected : list[str]   включённые ленты; разница с доступными → sources_missing

returns:
    picked            : list[dict]  отобранные материалы (oversample штук на источник)
    seed, sources_used, sources_available, sources_missing, reused_ids
    skip              : str | None  причина честного отказа (мало ядерных источников)
"""

import random

DEFAULT_AUX = ("self",)


def _item_key(item):
    """«source:id» — формат seen_items. Без id ключа нет (None): такое сырьё
   Seen-механика не различает — в отборе оно просто всегда «свежее»."""
    iid = item.get("id")
    if iid in (None, ""):
        return None
    return "%s:%s" % ((item.get("source") or "?"), iid)


def _norm_source(item):
    return (item.get("source") or "?").strip() or "?"


def run(inputs, env=None):
    items = list(inputs.get("items") or [])
    seed = inputs.get("seed")
    if seed is None:
        raise ValueError("pick_cross_sample: inputs['seed'] обязателен (чистота органа)")
    min_sources = int(inputs.get("min_sources", 2))
    oversample = max(1, int(inputs.get("oversample", 1)))
    aux = set(inputs.get("aux_sources") or DEFAULT_AUX)
    include_aux = bool(inputs.get("include_aux"))
    used = set(inputs.get("used_ids") or [])
    expected = list(inputs.get("sources_expected") or [])

    by_src = {}
    for it in items:
        if not isinstance(it, dict) or _item_key(it) is None:
            continue
        by_src.setdefault(_norm_source(it), []).append(it)

    available = sorted(by_src.keys())  # sorted → воспроизводимость между прогонами
    core = [s for s in available if s not in aux]
    missing = sorted(set(expected) - set(available)) if expected else []

    if len(core) < min_sources:
        return {
            "picked": [],
            "seed": seed,
            "sources_used": [],
            "sources_available": available,
            "sources_missing": missing,
            "reused_ids": [],
            "skip": "нужно >=%d источников с материалом (есть: %s)"
            % (min_sources, ", ".join(core) or "нет"),
        }

    rng = random.Random(seed)
    picked, reused = [], []
    pool_sources = [s for s in available if include_aux or s not in aux]
    for src in pool_sources:
        pool = sorted(by_src[src], key=_item_key)  # стабильный порядок ДО рандома
        fresh = [i for i in pool if _item_key(i) not in used]
        take_from = fresh if len(fresh) >= oversample else pool
        n = min(oversample, len(take_from))
        sampled = rng.sample(take_from, n)
        picked.extend(sampled)
        # честная метка — только про ВЫБРАННЫЕ материалы (не весь исчерпанный пул)
        reused.extend(_item_key(i) for i in sampled if _item_key(i) in used)

    return {
        "picked": picked,
        "seed": seed,
        "sources_used": sorted({_norm_source(i) for i in picked}),
        "sources_available": available,
        "sources_missing": missing,
        "reused_ids": sorted(set(reused)),
        "skip": None,
    }
