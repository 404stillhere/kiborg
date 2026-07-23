"""МОЗГ-генератор: ideate (порождение идей из сырья).

Вынесено из монолита wiring.py. seen_items/ideate — органы, патчатся в тестах
(test_wiring: `wiring.ideate.run = ...`), читаем через фасад. _content_llm —
чистый хелпер из wiring_runtime (нет зависимости от фасада, импортируем напрямую).
"""

import re

from wiring_runtime import _content_llm

# ПРОИСХОЖДЕНИЕ идеи (A5): item-источник приписывается post-factum по Jaccard значимых
# слов title. Стоп-слово-набор УЖЕ служебный (предлоги/союзы/артикли), БЕЗ предметно-общих
# «бот/система/приложение» — в отличие от store._STOP (дедуп), provenance НАДО видеть
# совпадение по доменным словам: «бот» в идее и в item'е реально указывает на связь.
# Семантика другая, чем у дедупа: там «бот» — шум (схлопывает разные проекты), здесь — сигнал.
_PROV_STOP = {
    "для",
    "на",
    "с",
    "и",
    "в",
    "по",
    "из",
    "о",
    "от",
    "до",
    "за",
    "к",
    "у",
    "а",
    "но",
    "или",
    "же",
    "ли",
    "бы",
    "что",
    "как",
    "это",
    "при",
    "об",
    "во",
    "со",
    "не",
    "без",
    "the",
    "a",
    "an",
    "for",
    "of",
    "to",
    "and",
    "with",
    "in",
    "on",
    "at",
    "by",
    "or",
    "as",
    "is",
    "be",
}
# Порог Jaccard для приписывания источника. Ниже — считаем, что модель синтезировала
# новую идею (заголовок был лишь толчком), и не навязываем ложный source. Для provenance
# (в отличие от дедупа) нужен мягкий порог: модель синтезирует — даже 1 значимое совпадение
# из 5 слов (=0.2) уже сигнал. Ложный source менее вреден чем в дедупе (юзер видит title и
# source_title рядом, сам рассудит), поэтому порог мягче.
_PROV_THRESHOLD = 0.2


def _prov_tokens(text):
    """Значимые слова текста (нижний регистр, без стоп-слов) — для Jaccard provenance."""
    return set(t for t in re.findall(r"[a-zа-яё0-9]+", (text or "").lower()) if t not in _PROV_STOP)


def _jaccard(a, b):
    """Jaccard по множествам. Пустые множества → 0 (не считаем пустоту похожей)."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _attach_provenance(ideas, items):
    """A5: приписать каждой идее item-источник по лучшему Jaccard(title) ≥ порога.

    Промпт ideate отдаёт модели ТОЛЬКО title item'ов — почему Jaccard на title корректен:
    модель не видит url/source/id, значит новый текст идеи мог родиться только из title.
    Идея без словаря / без title пропускается (source не навязываем).
    """
    if not ideas or not items:
        return
    # кэш токенов item'ов — один проход по пулу источников, не O(ideas×items) парсингов
    pool = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = it.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        toks = _prov_tokens(title)
        if not toks:
            continue
        pool.append((toks, it))
    if not pool:
        return
    for idea in ideas:
        if not isinstance(idea, dict):
            continue
        itoks = _prov_tokens(idea.get("title", ""))
        if not itoks:
            continue
        best_item, best_j = None, 0.0
        for toks, it in pool:
            j = _jaccard(itoks, toks)
            if j > best_j:
                best_j, best_item = j, it
        if best_item is not None and best_j >= _PROV_THRESHOLD:
            idea["inspired_by"] = best_item.get("id")
            idea["source_name"] = best_item.get("source")
            idea["source_url"] = best_item.get("url")
            idea["source_title"] = best_item.get("title")


def _run_ideate(inputs, env):
    import wiring

    inp = inputs or {}
    # ПАМЯТЬ — работа Мозга, не Глаз (2026-07-13, переехало из _run_collect). Фильтр «уже
    # видели» — ТОЛЬКО когда явно попросили (харвест ставит флаг в env). Интерактивный
    # «приноси идеи» (панель, ручной клик) флаг не ставит — юзер жмёт кнопку, ожидая идей
    # СЕЙЧАС, а не «а тут всё уже старое, пропускаю». filter_fresh отмечает виденным ровно
    # то, что реально уходит на генерацию — не раньше.
    fresh = None
    if env.get("filter_seen_items") and inp.get("items"):
        inp = dict(inp)
        fresh = wiring.seen_items.filter_fresh(inp["items"], mark=False)  # фильтруем БЕЗ пометки
        inp["items"] = fresh
    e = {"k": 8}  # генерим 8 кандидатов — баланс: разнообразие без размытия качества и без мультипликатора оркестра
    llm = _content_llm(env)
    if llm:
        e["llm"] = llm
    if env.get("direction"):
        e["direction"] = env["direction"]  # руль темы долетает до генератора
    if env.get("on_progress"):
        e["on_progress"] = env["on_progress"]  # живой суб-прогресс долетает до органа (иначе молчит)
    out = wiring.ideate.run(inp, e)
    # Метим сырьё виденным ТОЛЬКО ПОСЛЕ генерации и лишь если она удалась. При живом ключе
    # (llm_mode) осечка парса / обрыв даёт brain='stub' — НЕ метим, чтобы посты не сгорели зря:
    # сбой транзиентный, повторим на следующем тике (раньше метили ДО генерации — сжигали). Без
    # ключа stub ожидаем — метим как обычно, чтобы не крутить одни и те же заголовки.
    if fresh:
        ideas = out.get("ideas") or []
        produced_real = any(isinstance(i, dict) and i.get("brain") != "stub" for i in ideas)
        if produced_real or not callable(llm):
            wiring.seen_items.mark_seen(fresh)
    # A5 PROVENANCE: приписываем item-источник по Jaccard title (промпт даёт модели только
    # title — post-factum сопоставление корректно). Источник items берём из ИСХОДНОГО inputs
    # (до filter_seen_items), чтобы идея из отфильтрованного item'а тоже получила свой source.
    _attach_provenance(out.get("ideas") or [], inp.get("items") or inputs and inputs.get("items"))
    # PROVIDER — кто РЕАЛЬНО ответил в генераторе (muse-spark/deepseek/nemotron — цепочка
    # closerouter, см. keychain._SPEC). Полезно видеть, какое плечо сработало: muse-spark=первичная,
    # остальное=фолбэк при отлёте первичной. ask_llm.last_provider ставит _run_chain от organ.js
    # result.provider; поднимаем в out органа → orchestrator пробросит в run-выхлоп →
    # harvest._degrade_note рендерит «модель=…». Только при живой модели (stub-режим без ключа
    # провайдера не имеет).
    if callable(llm):
        try:
            import ask_llm as _ask

            prov = _ask.last_provider
        except Exception:
            prov = ""
        if prov:
            out["provider"] = prov
    return out
